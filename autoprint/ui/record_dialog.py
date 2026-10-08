"""Мастер «Записать звук клавиатуры»: любой пользователь записывает свою клавиатуру микрофоном
и получает свой набор звуков клавиш.

Шаги: микрофон → обычные клавиши → пробел → Enter → название. Пробел и Enter можно пропустить —
тогда они звучат как обычные клавиши, ниже по тону. Готовый набор сохраняется в data/sounds/custom/
(см. recorder.save_custom_pack) и появляется в sounds.available_styles().

Пока идёт запись (и пока на первом шаге слушается микрофон), нажатия клавиш до кнопок окна
не доходят: пробел и Enter иначе «нажали» бы кнопку с фокусом.

    python -m autoprint.ui.record_dialog
"""
from __future__ import annotations

import math
import random
import shutil
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPainter
from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import sounds
from ..recorder import RATE, AudioRecorder, cut_clip, filter_hits, hit_spans, normalize_groups, save_custom_pack
from .theme import theme
from .widgets import Card, Note, Row, accent_button, button, hsep, label, repolish

DEFAULT_NAME = "Моя клавиатура"
MAX_RECORD_S = 45          # запись останавливается сама — вдруг про «Стоп» забыли
KIND_TARGET = {"key": 8, "space": 4, "enter": 4}   # сколько ударов хорошо бы найти
MIN_KEY_HITS = 3           # меньше — набор не собрать
_KEY_EVENTS = (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride)

PAGES = [
    ("mic", "Микрофон", "Запишем, как звучит ваша клавиатура, — и AutoPrintCode будет печатать её звуком. "
                        "Понадобятся микрофон и пара минут."),
    ("key", "Обычные клавиши", "Нажмите «Начать запись» и спокойно нажимайте разные клавиши с буквами — "
                               "около 15 раз, с паузой примерно в полсекунды. Потом нажмите «Стоп»."),
    ("space", "Пробел", "Так же: около 8 нажатий пробела с паузами. Можно пропустить — тогда пробел будет "
                        "звучать как обычная клавиша, только ниже и громче."),
    ("enter", "Enter", "Около 8 нажатий Enter с паузами. Можно пропустить — тогда Enter будет звучать "
                       "как обычная клавиша, только ниже и громче."),
    ("save", "Готово", "Назовите набор и послушайте, как он звучит. После сохранения он появится "
                       "в списке «Какая клавиатура»."),
]


def _db(level: float) -> float:
    """Уровень 0..1 → доля шкалы −60…0 дБ (так слышно и тихий шорох, и удар)."""
    if level <= 1e-6:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(level) + 60) / 60))


def _hits_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "удар"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "удара"
    return "ударов"


# ---------------------------------------------------------------- рисованные элементы

class StepDots(QWidget):
    """Шаги мастера: пройденные и текущий — янтарные."""

    def __init__(self, count: int, parent=None) -> None:
        super().__init__(parent)
        self.count, self.current = count, 0
        self.setFixedSize(count * 28 - 6, 6)
        theme.changed.connect(self.update)

    def set_current(self, i: int) -> None:
        self.current = i
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.count):
            p.setBrush(theme.c("accent") if i <= self.current else theme.c("stroke_strong"))
            p.drawRoundedRect(QRectF(i * 28, 1, 22, 4), 2, 2)
        p.end()


class LevelMeter(QWidget):
    """Индикатор уровня микрофона: быстро вверх, плавно вниз."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._shown = 0.0
        self._hot = False
        self.setFixedHeight(10)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def set_level(self, level: float) -> None:
        self._shown = max(_db(level), self._shown - 0.06)
        self._hot = level > 0.97
        self.update()

    def reset(self) -> None:
        self._shown, self._hot = 0.0, False
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(0, 1, self.width(), self.height() - 2)
        p.setBrush(theme.c("stroke"))
        p.drawRoundedRect(r, 4, 4)
        if self._shown > 0:
            p.setBrush(theme.c("live" if self._hot else "accent"))
            p.drawRoundedRect(QRectF(0, 1, max(8.0, r.width() * self._shown), r.height()), 4, 4)
        p.end()


class WaveView(QWidget):
    """Запись: во время записи — бегущий уровень, после — огибающая и найденные удары (янтарным)."""
    BAR, GAP = 3, 2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(86)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._live: deque[float] = deque(maxlen=400)
        self._env: list[float] = []      # пик по 2 мс
        self._hit: list[bool] = []       # окно внутри найденного удара
        self._mode = "empty"
        theme.changed.connect(self.update)

    def clear(self) -> None:
        self._mode = "empty"
        self._env, self._hit = [], []
        self.update()

    def start_live(self) -> None:
        self._mode = "live"
        self._live.clear()
        self.update()

    def push(self, level: float) -> None:
        if self._mode == "live":
            self._live.append(level)
            self.update()

    def show_result(self, samples: list[float], spans: list[tuple[int, int]]) -> None:
        win = max(1, round(RATE * 0.002))
        self._env = [max(max(seg), -min(seg)) for seg in (samples[i:i + win] for i in range(0, len(samples), win))]
        self._hit = [False] * len(self._env)
        for a, b in spans:
            for w in range(a // win, min(len(self._hit), b // win + 1)):
                self._hit[w] = True
        self._mode = "result" if self._env else "empty"
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("bg"))
        p.drawRoundedRect(QRectF(0, 0, w, h), 5, 5)
        cy, half = h / 2, h / 2 - 8
        step = self.BAR + self.GAP
        bars = max(1, int((w - 16) // step))
        if self._mode == "empty":
            p.setBrush(theme.c("stroke_strong"))
            p.drawRect(QRectF(8, cy - 0.5, w - 16, 1))
            p.setPen(theme.c("faint"))
            p.drawText(QRectF(0, 0, w, cy - 6), Qt.AlignmentFlag.AlignCenter, "Здесь появится запись")
            p.end()
            return
        if self._mode == "live":
            levels = list(self._live)[-bars:]
            cols = [(lv, True) for lv in levels]
            x0 = 8 + (bars - len(cols)) * step   # новые справа, старые уезжают влево
        else:
            n = len(self._env)
            cols = []
            for i in range(bars):
                a, b = i * n // bars, max(i * n // bars + 1, (i + 1) * n // bars)
                cols.append((max(self._env[a:b]), any(self._hit[a:b])))
            x0 = 8
        for i, (lv, hot) in enumerate(cols):
            bh = max(1.0, _db(lv) * half)
            p.setBrush(theme.c("accent") if hot else theme.c("faint"))
            p.drawRoundedRect(QRectF(x0 + i * step, cy - bh, self.BAR, 2 * bh), 1.5, 1.5)
        p.end()


# ---------------------------------------------------------------- прослушивание

class _Preview(QObject):
    """Проигрывает отрезки по очереди: пишет их во временные WAV и запускает QSoundEffect."""

    def __init__(self, volume: float, parent=None) -> None:
        super().__init__(parent)
        self._dir = Path(tempfile.mkdtemp(prefix="autoprint-rec-"))
        self._volume = volume
        self._n = 0
        self._effects: list[QSoundEffect] = []
        self._timers: list[QTimer] = []

    def play(self, clips: list[list[float]], gap_ms: int) -> None:
        self.stop()
        self._dir.mkdir(parents=True, exist_ok=True)   # после cleanup() окно могли открыть снова
        for i, c in enumerate(clips):
            self._n += 1   # всегда новый файл: прежний может ещё держать проигрыватель
            path = self._dir / f"p{self._n}.wav"
            sounds.write_wav(path, c)
            e = QSoundEffect(self)
            e.setSource(QUrl.fromLocalFile(str(path)))
            e.setVolume(self._volume)
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(e.play)
            t.start(120 + i * gap_ms)   # 120 мс — файл успевает загрузиться
            self._effects.append(e)
            self._timers.append(t)

    def stop(self) -> None:
        for t in self._timers:
            t.stop()
            t.deleteLater()
        for e in self._effects:
            e.stop()
            e.deleteLater()
        self._timers, self._effects = [], []

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self._dir, ignore_errors=True)


# ---------------------------------------------------------------- мастер

class RecordDialog(QDialog):
    """Мастер записи. После «Сохранить»: style_key — ключ нового набора, сигнал created(ключ)."""
    created = Signal(str)

    def __init__(self, parent=None, volume: int = 60) -> None:
        super().__init__(parent)
        self.setWindowTitle("Записать звук клавиатуры")
        self.setModal(True)
        self.resize(660, 600)
        self.setMinimumSize(560, 540)
        self.style_key: str | None = None
        self._raw: dict[str, list[list[float]]] = {"key": [], "space": [], "enter": []}
        self._skipped: set[str] = set()
        self._saved = False
        self._guard = ""            # "" | "monitor" | "record" — что делать с нажатиями клавиш
        self._recording = ""        # вид клавиш, который сейчас пишется
        self._started = 0.0
        self._tones: dict[QLabel, str] = {}
        self.rec = AudioRecorder(self)
        self.rec.level.connect(self._on_level)
        self.rec.error.connect(self._on_rec_error)
        self.preview = _Preview(max(0, min(100, volume)) / 100, self)
        self._media = QMediaDevices(self)
        self._media.audioInputsChanged.connect(self._fill_devices)
        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._on_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(0)
        # неприметный получатель фокуса: пока идёт запись, фокус здесь, а не на кнопке
        self._sink = QWidget(self)
        self._sink.setFixedSize(1, 1)
        self._sink.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.dots = StepDots(len(PAGES))
        head.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignVCenter)
        self.step_lbl = label("", "groupTitle")
        head.addWidget(self.step_lbl)
        head.addStretch(1)
        root.addLayout(head)
        root.addSpacing(10)
        self.title_lbl = label("", "pageTitle")
        root.addWidget(self.title_lbl)
        root.addSpacing(4)
        self.lede_lbl = label("", "lede", wrap=True)
        root.addWidget(self.lede_lbl)
        root.addSpacing(18)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_mic())
        self.rec_pages: dict[str, dict] = {}
        for kind in ("key", "space", "enter"):
            self.stack.addWidget(self._page_record(kind))
        self.stack.addWidget(self._page_save())
        root.addWidget(self.stack, 1)

        self.warn = QWidget()
        wl = QHBoxLayout(self.warn)
        wl.setContentsMargins(0, 10, 0, 0)
        self.warn_lbl = label("", "rowSub", wrap=True)
        wl.addWidget(self.warn_lbl, 1)
        self._tone(self.warn_lbl, "live")
        self.warn.hide()
        root.addWidget(self.warn)
        root.addSpacing(14)
        root.addWidget(hsep())
        root.addSpacing(14)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.cancel_btn = button("Отмена", quiet=True)
        self.cancel_btn.clicked.connect(self.reject)
        nav.addWidget(self.cancel_btn)
        nav.addStretch(1)
        self.back_btn = button("Назад")
        self.back_btn.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        nav.addWidget(self.back_btn)
        self.next_btn = accent_button("Далее")
        self.next_btn.setMinimumWidth(120)
        self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.next_btn)
        root.addLayout(nav)

        # Пробел и Enter не должны «нажимать» кнопки: ни кнопки по умолчанию, ни фокуса на кнопках
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self._retone)
        self._fill_devices()
        self._go(0)

    # ------------------------------------------------------------ страницы
    def _page_mic(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        c = Card()
        self.dev_combo = QComboBox()
        self.dev_combo.setMinimumWidth(300)
        self.dev_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dev_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # буквы с клавиатуры не листают список
        self.dev_combo.currentIndexChanged.connect(self._on_device)
        c.add(Row("sliders", "Микрофон", "Подойдёт тот, которым вы записываете видео", [self.dev_combo],
                  stack=True))
        self.meter = LevelMeter()
        lvl = Row("activity", "Уровень", "Нажмите любую клавишу — полоска должна подпрыгнуть", [self.meter],
                  stack=True)
        c.add(lvl)
        lay.addWidget(c)
        lay.addWidget(Note("Поставьте микрофон рядом с клавиатурой, в комнате — тишина. Дальше — три коротких "
                           "записи: обычные клавиши, пробел и Enter.<br><br>Микрофон Bluetooth-наушников "
                           "лучше не выбирать: пока он включён, Windows переводит наушники в режим гарнитуры — "
                           "звук становится тише и хуже, а в списке устройств появляются их «гарнитурные» "
                           "копии. После закрытия мастера всё возвращается."))
        lay.addStretch(1)
        return page

    def _page_record(self, kind: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        c = Card()
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(16, 14, 16, 14)
        il.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(8)
        start = accent_button("Начать запись")
        start.clicked.connect(self._start_record)
        stop = button("Стоп", "stop")
        stop.clicked.connect(self._stop_record)
        stop.hide()
        listen = button("Послушать", "play")
        listen.clicked.connect(lambda _=False, k=kind: self._listen(k))
        listen.setEnabled(False)
        top.addWidget(start)
        top.addWidget(stop)
        top.addWidget(listen)
        top.addStretch(1)
        clock = label("0:00", "mono")
        top.addWidget(clock)
        il.addLayout(top)
        wave = WaveView()
        il.addWidget(wave)
        status = label("Ещё ничего не записано", "rowSub", wrap=True)
        il.addWidget(status)
        c.lay.addWidget(inner)
        lay.addWidget(c)
        skip = None
        if kind != "key":
            skip = button("Пропустить — возьмём обычные клавиши", quiet=True)
            skip.clicked.connect(lambda _=False, k=kind: self._skip(k))
            row = QHBoxLayout()
            row.addWidget(skip)
            row.addStretch(1)
            lay.addLayout(row)
        lay.addStretch(1)
        self.rec_pages[kind] = {"start": start, "stop": stop, "listen": listen, "clock": clock, "wave": wave,
                                "status": status, "skip": skip}
        return page

    def _page_save(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        c = Card()
        self.name_edit = QLineEdit(DEFAULT_NAME)
        self.name_edit.setMaxLength(40)
        self.name_edit.setFixedWidth(260)
        self.name_edit.textChanged.connect(lambda _t: self._update_nav())
        self.name_edit.returnPressed.connect(lambda: self.next_btn.isEnabled() and self._next())
        c.add(Row("pencil", "Название", "Так набор будет называться в настройках", [self.name_edit]))
        lay.addWidget(c)
        c = Card()
        self.sum_rows = {"key": Row("keyboard", "Обычные клавиши", "—"), "space": Row("space", "Пробел", "—"),
                         "enter": Row("enter", "Enter", "—")}
        for r in self.sum_rows.values():
            c.add(r)
        play = button("Послушать", "play")
        play.clicked.connect(self._listen_all)
        c.add(Row("music", "Как это прозвучит", "Несколько нажатий подряд — как при печати", [play]))
        lay.addWidget(c)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------ навигация
    def _go(self, i: int) -> None:
        i = max(0, min(len(PAGES) - 1, i))
        if self._recording:
            return
        self.preview.stop()
        self.stack.setCurrentIndex(i)
        key, title, lede = PAGES[i]
        self.dots.set_current(i)
        self.step_lbl.setText(f"Шаг {i + 1} из {len(PAGES)}")
        self.title_lbl.setText(title)
        self.lede_lbl.setText(lede)
        self._hide_warn()
        if key == "mic":
            self._start_monitor()
        else:
            self._stop_monitor()
        if key == "save":
            self._fill_summary()
            self.name_edit.setFocus()
            self.name_edit.selectAll()
        else:
            self._sink.setFocus()
        self._update_nav()

    def _next(self) -> None:
        key = PAGES[self.stack.currentIndex()][0]
        if key == "save":
            self._save()
            return
        self._go(self.stack.currentIndex() + 1)

    def _skip(self, kind: str) -> None:
        self._raw[kind] = []
        self._skipped.add(kind)
        self._show_result(kind, [], [])
        self._go(self.stack.currentIndex() + 1)

    def _update_nav(self) -> None:
        i = self.stack.currentIndex()
        key = PAGES[i][0]
        busy = bool(self._recording)
        self.back_btn.setVisible(i > 0)
        self.back_btn.setEnabled(not busy)
        self.next_btn.setText("Сохранить" if key == "save" else "Далее")
        if key == "mic":
            ok = self.dev_combo.isEnabled()
        elif key == "key":
            ok = len(filter_hits(self._raw["key"])) >= MIN_KEY_HITS
        elif key == "save":
            ok = bool(self.name_edit.text().strip()) and bool(self._raw["key"])
        else:
            ok = True
        on = ok and not busy
        self.next_btn.setEnabled(on)
        if bool(self.next_btn.property("accent")) != on:   # янтарная кнопка без :disabled в QSS — как обычная
            self.next_btn.setProperty("accent", on)
            repolish(self.next_btn)
        for kind, w in self.rec_pages.items():
            w["start"].setVisible(self._recording != kind)
            w["stop"].setVisible(self._recording == kind)
            w["start"].setText("Записать заново" if self._raw[kind] else "Начать запись")
            w["listen"].setEnabled(not busy and bool(self._raw[kind]))
            if w["skip"] is not None:
                w["skip"].setEnabled(not busy)

    # ------------------------------------------------------------ микрофон
    def _fill_devices(self) -> None:
        current = self.dev_combo.currentData()
        default = AudioRecorder.default_device()
        devs = AudioRecorder.devices()
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        for dev_id, name in devs:
            self.dev_combo.addItem(name + (" — по умолчанию" if dev_id == default else ""), dev_id)
        if not devs:
            self.dev_combo.addItem("Микрофон не найден", None)
        i = self.dev_combo.findData(current) if current else -1
        self.dev_combo.setCurrentIndex(i if i >= 0 else max(0, self.dev_combo.findData(default)))
        self.dev_combo.setEnabled(bool(devs))
        self.dev_combo.blockSignals(False)
        if not devs:
            self._show_warn("Микрофон не найден: подключите его — он появится в списке сам")
        if self.stack.currentIndex() == 0 and self.isVisible():
            self._start_monitor()
        self._update_nav()

    def _device(self) -> str | None:
        return self.dev_combo.currentData()

    def _on_device(self, _i: int) -> None:
        if self.stack.currentIndex() == 0 and self.isVisible():
            self._start_monitor()

    def _start_monitor(self) -> None:
        """Шаг 1: только индикатор уровня, запись не копится."""
        if not self.isVisible() or not self.dev_combo.isEnabled():
            return
        self._hide_warn()
        self.meter.reset()
        if self.rec.start(self._device(), keep=False):
            self._set_guard("monitor")

    def _stop_monitor(self) -> None:
        if self.rec.recording and not self._recording:
            self.rec.stop()
        self.meter.reset()
        if not self._recording:
            self._set_guard("")

    def _on_level(self, level: float) -> None:
        self.meter.set_level(level)
        if self._recording:
            self.rec_pages[self._recording]["wave"].push(level)

    def _on_rec_error(self, msg: str) -> None:
        self._show_warn(msg)

    # ------------------------------------------------------------ запись
    def _start_record(self) -> None:
        kind = PAGES[self.stack.currentIndex()][0]
        if kind not in self.rec_pages or self._recording:
            return
        self.preview.stop()
        self._hide_warn()
        if not self.rec.start(self._device(), keep=True):
            return
        self._recording = kind
        self._skipped.discard(kind)
        self._started = time.monotonic()
        w = self.rec_pages[kind]
        w["wave"].start_live()
        w["clock"].setText("0:00")
        w["status"].setText("Идёт запись — нажимайте клавиши. Кнопки окна сейчас не реагируют на клавиатуру")
        self._tone(w["status"], "accent")
        self._set_guard("record")
        self._tick.start()
        self._update_nav()

    def _stop_record(self) -> None:
        kind = self._recording
        if not kind:
            return
        self._tick.stop()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            samples = self.rec.stop()
            spans = hit_spans(samples)
            dc = sum(samples) / len(samples) if samples else 0.0
            clips = [cut_clip(samples, a, b, RATE, dc) for a, b in spans]
        finally:
            QApplication.restoreOverrideCursor()
        self._recording = ""
        self._set_guard("")
        self._raw[kind] = clips
        self._show_result(kind, samples, spans)
        self._update_nav()

    def _show_result(self, kind: str, samples: list[float], spans: list[tuple[int, int]]) -> None:
        w = self.rec_pages[kind]
        if samples:
            w["wave"].show_result(samples, spans)
        else:
            w["wave"].clear()
            w["clock"].setText("0:00")
        clips = self._raw[kind]
        good = len(filter_hits(clips))
        noise = len(clips) - good
        target = KIND_TARGET[kind]
        if kind in self._skipped:
            text, tone = "Пропущено — будут звучать обычные клавиши, ниже и громче", ""
        elif not samples:
            text, tone = "Ещё ничего не записано", ""
        elif good == 0:
            text, tone = "Ударов не найдено. Нажимайте отчётливее или поставьте микрофон ближе", "live"
        elif good < target:
            text, tone = (f"Найдено: {good} {_hits_word(good)} — маловато, лучше записать ещё раз "
                          f"(хорошо бы {target} и больше)"), "live"
        else:
            text, tone = f"Найдено: {good} {_hits_word(good)} — хорошо", "ok"
        if noise and samples:
            text += f" · не похоже на удар и отброшено: {noise}"
        w["status"].setText(text)
        self._tone(w["status"], tone)

    def _on_tick(self) -> None:
        if not self._recording:
            return
        elapsed = time.monotonic() - self._started   # по часам: данных с микрофона может и не быть
        s = int(elapsed)
        self.rec_pages[self._recording]["clock"].setText(f"{s // 60}:{s % 60:02d}")
        if elapsed >= MAX_RECORD_S:
            self._stop_record()

    # ------------------------------------------------------------ прослушивание и сохранение
    def _listen(self, kind: str) -> None:
        clips = normalize_groups({kind: self._raw[kind]})[kind]
        self.preview.play(clips[:10], 260)

    def _variants(self) -> dict[str, list[list[float]]] | None:
        groups = normalize_groups(self._raw)
        if not groups["key"]:
            return None
        return sounds.custom_variants(groups, random.Random(), 4)

    def _listen_all(self) -> None:
        v = self._variants()
        if v is None:
            self._show_warn("Нет записей обычных клавиш — вернитесь на шаг 2")
            return
        seq = [v["key"][0], v["key"][1], v["key"][2], v["space"][0], v["key"][3], v["key"][1], v["enter"][0]]
        self.preview.play(seq, 150)

    def _fill_summary(self) -> None:
        for kind, row in self.sum_rows.items():
            n = len(filter_hits(self._raw[kind]))
            if n:
                row.sub.setText(f"Записано: {n} {_hits_word(n)}")
            elif kind == "key":
                row.sub.setText("Нет записей — вернитесь на шаг 2")
            else:
                row.sub.setText("Не записано — возьмём обычные клавиши, ниже по тону и громче")

    def _save(self) -> None:
        name = self.name_edit.text().strip() or DEFAULT_NAME
        groups = normalize_groups(self._raw)
        if not groups["key"]:
            self._show_warn("Нет записей обычных клавиш — вернитесь на шаг 2 и запишите их")
            return
        try:
            key = save_custom_pack(name, groups)
        except (OSError, ValueError) as e:
            self._show_warn(f"Не удалось сохранить набор: {e}")
            return
        self.style_key = key
        self._saved = True
        self.created.emit(key)
        self.accept()

    # ------------------------------------------------------------ клавиатура во время записи
    def _set_guard(self, mode: str) -> None:
        app = QApplication.instance()
        if mode and not self._guard:
            app.installEventFilter(self)
        elif not mode and self._guard:
            app.removeEventFilter(self)
        self._guard = mode
        if mode:
            self._sink.setFocus()

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if self._guard and ev.type() in _KEY_EVENTS and isinstance(obj, QWidget) and obj.window() is self:
            # пока только слушаем микрофон (шаг 1), Esc по-прежнему закрывает окно
            if (self._guard == "monitor" and ev.type() == QEvent.Type.KeyPress
                    and ev.key() == Qt.Key.Key_Escape):
                return False
            ev.accept()
            return True
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------ подписи и предупреждения
    def _tone(self, lb: QLabel, tone: str) -> None:
        self._tones[lb] = tone
        lb.setStyleSheet(f"color: {theme.css(tone)};" if tone else "")

    def _retone(self) -> None:
        for lb, tone in self._tones.items():
            lb.setStyleSheet(f"color: {theme.css(tone)};" if tone else "")

    def _show_warn(self, text: str) -> None:
        self.warn_lbl.setText(text)
        self.warn.show()

    def _hide_warn(self) -> None:
        self.warn.hide()

    # ------------------------------------------------------------ закрытие
    def _dirty(self) -> bool:
        return not self._saved and any(self._raw.values())

    def reject(self) -> None:
        if self._recording:
            self._tick.stop()
            self.rec.stop()
            self._recording = ""
            self._set_guard("")
            self._update_nav()
        if self._dirty() and self.isVisible():
            ask = QMessageBox.question(self, "Записать звук клавиатуры", "Закрыть без сохранения? Записи пропадут.",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                       QMessageBox.StandardButton.No)
            if ask != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def done(self, r: int) -> None:
        self._tick.stop()
        if self.rec.recording:
            self.rec.stop()
        self._recording = ""
        self._set_guard("")
        self.preview.cleanup()
        super().done(r)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.stack.currentIndex() == 0 and not self.rec.recording:
            QTimer.singleShot(0, self._start_monitor)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    theme.setup(app, "system")
    d = RecordDialog()
    d.created.connect(lambda key: print("Сохранён набор:", key, sounds.custom_dir(key)))
    d.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
