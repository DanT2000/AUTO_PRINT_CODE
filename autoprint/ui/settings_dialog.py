"""Окно настроек."""
from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QMessageBox, QPushButton, QSlider,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..hotkeys import parse_hotkey
from ..sounds import STYLES
from ..storage import PROFILE_IDE, PROFILES, Settings

HOTKEYS = [
    ("hotkey_toggle", "Старт / пауза / продолжить"),
    ("hotkey_restart", "Начать сначала"),
    ("hotkey_stop", "Остановить"),
    ("hotkey_next_block", "Следующий блок кода"),
    ("hotkey_prev_block", "Предыдущий блок кода"),
    ("hotkey_next_tab", "Следующая вкладка-образец"),
]


class _HotkeyEdit(QWidget):
    def __init__(self, value: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QKeySequenceEdit(QKeySequence(value))
        self.edit.setMaximumSequenceLength(1)
        clear = QPushButton("✕")
        clear.setFixedWidth(28)
        clear.setToolTip("Без хоткея")
        clear.clicked.connect(self.edit.clear)
        lay.addWidget(self.edit, 1)
        lay.addWidget(clear)

    def value(self) -> str:
        return self.edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)


def _spin(lo: int, hi: int, val: int, suffix: str = "", step: int = 1) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsDialog(QDialog):
    test_sound = Signal(str, int)   # (стиль, громкость)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        s = settings
        tabs = QTabWidget()

        # ---- хоткеи
        w = QWidget()
        f = QFormLayout(w)
        self.hk: dict[str, _HotkeyEdit] = {}
        for key, label in HOTKEYS:
            self.hk[key] = _HotkeyEdit(getattr(s, key))
            f.addRow(label + ":", self.hk[key])
        note = QLabel("Хоткеи глобальные — работают в любом окне и не доходят до него. "
                      "Удобны F-клавиши с Ctrl/Shift: они редко заняты в редакторах.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        f.addRow(note)
        tabs.addTab(w, "Хоткеи")

        # ---- печать
        w = QWidget()
        f = QFormLayout(w)
        self.cpm = _spin(30, 3000, s.cpm, " симв/мин", 10)
        self.jitter = _spin(0, 90, s.jitter, " %")
        self.nl_pause = _spin(0, 3000, s.newline_pause_ms, " мс", 10)
        self.punct = _spin(0, 1000, s.punct_pause_ms, " мс", 10)
        self.tab_w = _spin(1, 8, s.tab_width, " пробелов")
        self.key_gap = _spin(5, 200, s.key_gap_ms, " мс", 5)
        self.key_gap.setToolTip("Меньше — быстрее служебные нажатия и отступы, но медленные редакторы "
                                "(Блокнот Windows 11, браузерные IDE) начинают терять символы. "
                                "Если символы пропадают — увеличьте.")
        self.fast_indent = QCheckBox("Отступы печатать быстро")
        self.fast_indent.setChecked(s.fast_indent)
        self.whole_lines = QCheckBox("Выделение в образце расширять до целых строк")
        self.whole_lines.setChecked(s.selection_whole_lines)
        self.profile = QComboBox()
        for k, v in PROFILES.items():
            self.profile.addItem(v, k)
        self.profile.setCurrentIndex(self.profile.findData(s.profile))
        self.esc = QCheckBox("Esc перед Enter (закрыть подсказки). Не для Jupyter!")
        self.esc.setChecked(s.esc_before_enter)
        f.addRow("Скорость:", self.cpm)
        f.addRow("Разброс темпа:", self.jitter)
        f.addRow("Пауза после Enter:", self.nl_pause)
        f.addRow("Пауза после , ; : ) ]:", self.punct)
        f.addRow("Таб в образце =", self.tab_w)
        f.addRow("Мин. интервал нажатий:", self.key_gap)
        f.addRow(self.fast_indent)
        f.addRow(self.whole_lines)
        f.addRow("Профиль окна:", self.profile)
        f.addRow(self.esc)
        hint = QLabel("<b>IDE</b>: убирает автоотступы и автоскобки редактора, чтобы код "
                      "получился точно как в образце. <b>Блокнот</b>: печать «как есть».")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        f.addRow(hint)
        tabs.addTab(w, "Печать")

        # ---- звук
        w = QWidget()
        f = QFormLayout(w)
        self.snd = QCheckBox("Звук клавиш")
        self.snd.setChecked(s.sound_enabled)
        self.style = QComboBox()
        for k, v in STYLES.items():
            self.style.addItem(v, k)
        self.style.setCurrentIndex(max(0, self.style.findData(s.sound_style)))
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(s.sound_volume)
        test = QPushButton("▶ Проба")
        test.clicked.connect(lambda: self.test_sound.emit(self.style.currentData(), self.vol.value()))
        vrow = QHBoxLayout()
        vrow.addWidget(self.vol, 1)
        vrow.addWidget(test)
        f.addRow(self.snd)
        f.addRow("Звук:", self.style)
        f.addRow("Громкость:", vrow)
        tabs.addTab(w, "Звук")

        # ---- поведение
        w = QWidget()
        f = QFormLayout(w)
        self.hk_delay = _spin(0, 3000, s.hotkey_start_delay_ms, " мс", 50)
        self.countdown = _spin(0, 10, s.button_countdown_s, " с")
        self.minimize = QCheckBox("Сворачивать окно при старте кнопкой (фокус вернётся в прошлое окно)")
        self.minimize.setChecked(s.minimize_on_button_start)
        self.autopause = QCheckBox("Пауза, если сменилось активное окно")
        self.autopause.setChecked(s.autopause_on_focus_change)
        self.guard = QCheckBox("Пауза, если во время печати нажата клавиша или кнопка мыши\n"
                               "(случайная клавиша не попадёт в код)")
        self.guard.setChecked(s.guard_enabled)
        self.advance = QCheckBox("По окончании переходить к следующему блоку кода")
        self.advance.setChecked(s.auto_advance)
        self.on_top = QCheckBox("Окно поверх остальных")
        self.on_top.setChecked(s.always_on_top)
        f.addRow("Задержка старта по хоткею:", self.hk_delay)
        f.addRow("Отсчёт при старте кнопкой:", self.countdown)
        for cb in (self.minimize, self.autopause, self.guard, self.advance, self.on_top):
            f.addRow(cb)
        tabs.addTab(w, "Поведение")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(bb)
        self.result_settings: Settings | None = None
        self._base = s

    def _accept(self) -> None:
        values = {k: e.value() for k, e in self.hk.items()}
        used = {}
        for k, v in values.items():
            if not v:
                continue
            if parse_hotkey(v) is None:
                QMessageBox.warning(self, "Хоткей", f"Сочетание «{v}» не поддерживается.")
                return
            if v in used:
                QMessageBox.warning(self, "Хоткей", f"«{v}» назначен дважды.")
                return
            used[v] = k
        if not values["hotkey_toggle"]:
            QMessageBox.warning(self, "Хоткей", "Нужен хоткей «Старт / пауза».")
            return
        s = Settings(**asdict(self._base))
        for k, v in values.items():
            setattr(s, k, v)
        s.cpm = self.cpm.value()
        s.jitter = self.jitter.value()
        s.newline_pause_ms = self.nl_pause.value()
        s.punct_pause_ms = self.punct.value()
        s.tab_width = self.tab_w.value()
        s.key_gap_ms = self.key_gap.value()
        s.fast_indent = self.fast_indent.isChecked()
        s.selection_whole_lines = self.whole_lines.isChecked()
        s.profile = self.profile.currentData() or PROFILE_IDE
        s.esc_before_enter = self.esc.isChecked()
        s.sound_enabled = self.snd.isChecked()
        s.sound_style = self.style.currentData()
        s.sound_volume = self.vol.value()
        s.hotkey_start_delay_ms = self.hk_delay.value()
        s.button_countdown_s = self.countdown.value()
        s.minimize_on_button_start = self.minimize.isChecked()
        s.autopause_on_focus_change = self.autopause.isChecked()
        s.guard_enabled = self.guard.isChecked()
        s.auto_advance = self.advance.isChecked()
        s.always_on_top = self.on_top.isChecked()
        self.result_settings = s
        self.accept()
