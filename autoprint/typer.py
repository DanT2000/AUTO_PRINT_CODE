"""Движок печати: превращает текст в последовательность «единиц» и печатает их в активное окно.

Профиль IDE компенсирует «умные» редакторы (VS Code/Monaco, CodeMirror, Jupyter):
  * перед Enter и перед закрывающими ) ] } " ' ` — Shift+End, пробел, Backspace:
    стирает всё, что редактор сам дописал справа от курсора (автоскобки, автокавычки)
    и закрывает подсказки, чтобы Enter не принял автодополнение. На «перепечатывание»
    автоскобок не полагаемся: в CodeMirror 6 оно ломает тройные кавычки;
  * после Enter — Shift+Home, пробел, Backspace: убирает автоотступ, после чего
    отступ строки печатается ровно как в образце.
Трюк «пробел + Backspace» безопасен и когда выделение пустое (ничего не меняет),
в отличие от Delete, который склеил бы строки.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from . import winapi as w
from .human import humanize
from .storage import PROFILE_IDE, Settings

# Минимальный интервал между нажатиями задаётся настройкой key_gap_ms.
# Замер на Блокноте Windows 11: при 8–12 мс символы теряются и переставляются,
# при 30 мс — набор точный. Редакторы на Electron/браузере ещё чувствительнее.
PUNCT = set(",;:)]}>")
# Профиль IDE: перед этими символами очищается правая часть строки (там может быть
# только то, что редактор дописал сам — автоскобки и автокавычки).
CLEAR_BEFORE = set(")]}\"'`")

log = logging.getLogger(__name__)

IDLE, COUNTDOWN, RUNNING, PAUSED, FINISHED = "idle", "countdown", "running", "paused", "finished"
LINE_WAIT = "line_wait"   # печать по строкам: строка готова, ждём Enter пользователя (или хоткей)

# клавиши перемещения (единицы «nav», печать по шагам) → (vk, модификаторы)
NAV_KEYS = {"ctrl_home": (w.VK_HOME, (w.VK_CONTROL,)), "ctrl_end": (w.VK_END, (w.VK_CONTROL,)),
            "down": (w.VK_DOWN, ()), "up": (w.VK_UP, ()), "end": (w.VK_END, ()), "enter_raw": (w.VK_RETURN, ())}


@dataclass
class Unit:
    kind: str            # char | fast | tab | back | newline | cleanup | nav
    text: str = ""
    src_end: int = 0     # позиция в исходном тексте после этой единицы
    k: float = 1.0       # множитель задержки после единицы (имитация ручного ввода)
    pause: float = 0.0   # пауза перед единицей, с (обдумывание)
    wait: bool = False   # newline: печать по строкам — перед ним ждём Enter пользователя


def build_units(text: str, settings: Settings, line_wait: bool = False, cleanup: bool = True) -> list[Unit]:
    """Текст → единицы печати. line_wait — печать по строкам: после каждой непустой строки ждать Enter
    (пустые строки идут сами). cleanup — в профиле IDE в конце убрать то, что редактор дописал справа."""
    tab = " " * max(1, settings.tab_width)
    units: list[Unit] = []
    src = 0
    lines = text.split("\n")
    for li, line in enumerate(lines):
        if li > 0:
            src += 1
            units.append(Unit("newline", "\n", src, wait=line_wait and bool(lines[li - 1].strip())))
        m = len(line) - len(line.lstrip(" \t"))
        if m and settings.indent_with_tab:
            # как человек: Tab на каждый уровень отступа, остаток — пробелами
            col, cols = 0, []      # cols[i] — ширина отступа после i+1 исходных символов
            for ch in line[:m]:
                col += len(tab) if ch == "\t" else 1
                cols.append(col)
            done = 0               # сколько исходных символов отступа уже набрано
            for level in range(1, col // len(tab) + 1):
                done = next(i for i, c in enumerate(cols) if c >= level * len(tab)) + 1
                units.append(Unit("tab", "\t", src + done))
            for i in range(col % len(tab)):
                units.append(Unit("char", " ", src + min(m, done + i + 1)))
        elif m:
            indent = line[:m].replace("\t", tab)
            if settings.fast_indent:
                units.append(Unit("fast", indent, src + m))
            else:
                for i, ch in enumerate(line[:m]):
                    units.append(Unit("char", tab if ch == "\t" else ch, src + i + 1))
        for i, ch in enumerate(line[m:], start=m):
            units.append(Unit("char", tab if ch == "\t" else ch, src + i + 1))
        src += len(line)
    if settings.human_typing:
        units = humanize(units, settings)
    if cleanup and settings.profile == PROFILE_IDE and units:
        units.append(Unit("cleanup", "", src))
    return units


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class TypingEngine(QObject):
    state_changed = Signal(str)
    progress = Signal(int, int)        # (позиция в исходном тексте, длина)
    message = Signal(str)
    countdown = Signal(int)
    sound = Signal(str)
    finished = Signal()
    target = Signal(str)               # имя exe окна, в которое идёт печать (для пульта)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.state = IDLE
        self._text = ""
        self._units: list[Unit] = []
        self._pos = 0
        self._gen = 0
        self._run = threading.Event()
        self._resume_delay = 0.0
        self._resume_countdown = 0
        self._thread: threading.Thread | None = None
        self._builder = None   # как собрать единицы заново (печать по строкам / по шагам)
        self._target_hwnd = 0
        self._line_go = threading.Event()   # печать по строкам: «дальше» (Enter пользователя или хоткей)
        self._line_by_enter = False
        self.job_key = None    # чем занят движок (id блока) — для UI

    # ------------------------------------------------------------ API (GUI-поток)
    def load(self, text: str, job_key=None, builder=None) -> None:
        """Загружает новый текст. Текущая печать прерывается.
        builder(settings) → единицы — если печатать нужно не «как есть» (по строкам, по шагам);
        тогда text задаёт только длину для шкалы прогресса (позиции src_end — в нём)."""
        self._cancel()
        self._text = normalize(text)
        self._builder = builder
        self._units = self._build()
        self._pos = 0
        self.job_key = job_key
        self._set_state(IDLE)
        self.progress.emit(0, len(self._text))

    def _build(self) -> list[Unit]:
        return self._builder(self.settings) if self._builder else build_units(self._text, self.settings)

    def continue_line(self, by_enter: bool, delay: float = 0.0, countdown: int = 0) -> bool:
        """Печать по строкам: продолжить со следующей строки. by_enter — пользователь сам нажал Enter
        (он уже дошёл до редактора, повторно не нажимаем). Enter в другом окне не считается.
        Хоткей или кнопка — как продолжение после паузы: delay/countdown, ожидание отпускания Ctrl/Shift."""
        if self.state != LINE_WAIT:
            return False
        if by_enter and w.foreground_window() != self._target_hwnd:
            return False
        self._line_by_enter = by_enter
        self._resume_delay, self._resume_countdown = delay, countdown
        self._line_go.set()
        return True

    @property
    def has_job(self) -> bool:
        return bool(self._units)

    def start(self, delay: float = 0.0, countdown: int = 0) -> None:
        """Старт с текущей позиции (с начала, если закончили)."""
        if not self._units:
            self.message.emit("Нечего печатать: выберите блок кода с текстом.")
            return
        if self.state == PAUSED:
            self.resume(delay, countdown)
            return
        if self.state in (RUNNING, COUNTDOWN):
            return
        if self.state == FINISHED:
            self._pos = 0
        self._cancel()
        self._gen += 1
        self._run.set()
        self._thread = threading.Thread(target=self._worker, args=(self._gen, delay, countdown), daemon=True)
        self._thread.start()

    def pause(self, reason: str = "хоткей/кнопка") -> None:
        if self.state in (RUNNING, COUNTDOWN):
            self._run.clear()
            self._set_state(PAUSED)
            log.info("Пауза (%s) на %d/%d", reason, self._pos, len(self._units))

    def resume(self, delay: float = 0.0, countdown: int = 0) -> None:
        if self.state != PAUSED:
            return
        if not (self._thread and self._thread.is_alive()):
            self._set_state(IDLE)
            self.start(delay, countdown)
            return
        self._resume_delay = delay
        self._resume_countdown = countdown
        self._run.set()

    def toggle(self, delay: float = 0.0, countdown: int = 0) -> None:
        if self.state == LINE_WAIT:
            self.continue_line(False, delay, countdown)
        elif self.state in (RUNNING, COUNTDOWN):
            self.pause()
        else:
            self.start(delay, countdown)

    def restart(self, delay: float = 0.0, countdown: int = 0) -> None:
        self._cancel()
        self._pos = 0
        self._set_state(IDLE)
        self.progress.emit(0, len(self._text))
        self.start(delay, countdown)

    def stop(self) -> None:
        if self.state not in (IDLE,):
            log.info("Стоп на %d/%d", self._pos, len(self._units))
        self._cancel()
        self._pos = 0
        self._set_state(IDLE)
        self.progress.emit(0, len(self._text))

    def rebuild(self) -> None:
        """Пересобрать единицы после смены настроек (только если печать не идёт)."""
        if self.state in (IDLE, FINISHED) and self._text:
            self._units = self._build()
            self._pos = 0

    # ------------------------------------------------------------ внутреннее
    def _cancel(self) -> None:
        self._gen += 1
        self._run.set()  # разбудить поток, чтобы он увидел смену поколения
        self._line_go.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(1.0)
        self._thread = None
        self._run.clear()

    def _set_state(self, st: str) -> None:
        if self.state != st:
            self.state = st
            self.state_changed.emit(st)

    def _wset(self, gen: int, st: str) -> bool:
        """Смена состояния из рабочего потока — только пока его печать не отменена. Поток, который
        «Стоп» не дождался (ждал отпускания Ctrl), иначе вернул бы «Печатает» уже остановленной печати —
        и защита от нажатий глотала бы клавиши. RUNNING с учётом паузы, поставленной в это же время."""
        if not self._alive(gen):
            return False
        if st == RUNNING and not self._run.is_set():
            st = PAUSED
        self._set_state(st)
        if st == RUNNING and not self._run.is_set():   # pause() успела между проверкой и записью
            self._set_state(PAUSED)
        return True

    def _alive(self, gen: int) -> bool:
        return gen == self._gen

    def _sleep(self, gen: int, seconds: float) -> bool:
        """Сон, прерываемый остановкой. False — если печать отменена."""
        end = time.perf_counter() + seconds
        while True:
            if not self._alive(gen):
                return False
            left = end - time.perf_counter()
            if left <= 0:
                return True
            time.sleep(min(left, 0.05))

    def _think(self, gen: int, seconds: float) -> bool:
        """Пауза-обдумывание: обрывается, если поставили на паузу. False — если печать отменена."""
        end = time.perf_counter() + seconds
        while self._run.is_set():
            if not self._alive(gen):
                return False
            left = end - time.perf_counter()
            if left <= 0:
                break
            time.sleep(min(left, 0.05))
        return self._alive(gen)

    def _prepare(self, gen: int, delay: float, countdown: int, next_line: bool = False) -> int | None:
        """Отсчёт, ожидание отпускания модификаторов, захват целевого окна.
        next_line — продолжение печати по строкам хоткеем: в журнал не пишем (это каждая строка)."""
        if countdown > 0:
            self._wset(gen, COUNTDOWN)
            for n in range(countdown, 0, -1):
                self.countdown.emit(n)
                if not self._think(gen, 1.0):   # секунда отсчёта; обрывается паузой
                    return None
                if not self._run.is_set():
                    log.info("Отсчёт прерван паузой")
                    return None
            self.countdown.emit(0)
        if not w.wait_modifiers_released():
            log.warning("Не начато: модификаторы удерживаются дольше 5 с")
            self.message.emit("Отпустите Ctrl/Alt/Shift — печать не начата.")
            return None
        if not self._alive(gen):   # «Стоп», пока ждали отпускания клавиш
            return None
        if delay and not self._sleep(gen, delay):
            return None
        hwnd = w.foreground_window()
        if w.is_own_window(hwnd):
            log.info("Не начато: в фокусе окно самого AutoPrintCode")
            self.message.emit("Курсор стоит в окне AutoPrintCode. Перейдите в целевое окно "
                              "и нажмите хоткей ещё раз.")
            return None
        if not w.self_elevated() and w.window_elevated(hwnd):
            log.warning("Не начато: окно с правами администратора %s", w.describe_window(hwnd))
            self.message.emit("Окно запущено от имени администратора — Windows не пропустит в него ввод. "
                              "Запустите AutoPrintCode тоже от имени администратора.")
            return None
        if not next_line:
            log.info("Печать %s с %d/%d → %s · профиль=%s · %d симв/мин",
                     "продолжена" if self._pos else "начата", self._pos, len(self._units),
                     w.describe_window(hwnd), self.settings.profile, self.settings.cpm)
        self.target.emit(w.window_process_name(hwnd))
        self._target_hwnd = hwnd
        return hwnd

    def _pause_from_worker(self, gen: int) -> None:
        if self._alive(gen):
            self._run.clear()
            self._wset(gen, PAUSED)

    def _worker(self, gen: int, delay: float, countdown: int) -> None:
        target = self._prepare(gen, delay, countdown)
        if target is None:
            if self._wset(gen, PAUSED if self._pos else IDLE):
                self._run.clear()
            return
        # паузу могли поставить, пока ждали отпускания клавиш: тогда не «Печатает», а «Пауза» —
        # иначе включится защита от нажатий и проглотит следующую клавишу пользователя (это делает _wset)
        if not self._wset(gen, RUNNING):
            return
        total = len(self._text)
        thought = -1    # для какой единицы пауза-обдумывание уже выдержана
        waited = -1     # перед какой единицей «дальше» (Enter пользователя или хоткей) уже получено
        entered = -1    # перед какой единицей пользователь сам нажал Enter — повторно не нажимаем
        while self._alive(gen) and self._pos < len(self._units):
            if not self._run.is_set():
                self._run.wait()
                if not self._alive(gen):
                    return
                target = self._prepare(gen, self._resume_delay, self._resume_countdown)
                if target is None:
                    self._pause_from_worker(gen)
                    continue
                if not self._wset(gen, RUNNING) or self.state != RUNNING:   # снова пауза, пока готовились
                    continue
            unit = self._units[self._pos]
            if unit.wait and waited != self._pos:
                # печать по строкам: строка набрана — ждём Enter пользователя или хоткей
                if not self._wait_line(gen):
                    return
                waited = self._pos
                if self._line_by_enter:
                    entered = self._pos
                    time.sleep(self._gap() * 2)   # Enter уже в редакторе — дать ему вставить автоотступ
                else:
                    # хоткей или кнопка «Дальше» — как продолжение после паузы: дождаться, пока отпустят
                    # Ctrl/Shift (иначе Enter уйдёт как Ctrl+Enter, а Shift+End — как Ctrl+Shift+End и
                    # выделит весь хвост файла), проверить окно, при старте кнопкой — отсчёт
                    target = self._prepare(gen, self._resume_delay, self._resume_countdown, next_line=True)
                    if target is None:
                        self._pause_from_worker(gen)
                        continue
                if not self._wset(gen, RUNNING) or self.state != RUNNING:   # паузу поставили, пока готовились
                    continue
            if unit.pause and thought != self._pos:
                # обдумывание: прерывается паузой и остановкой; после него заново проверяем окно
                if not self._think(gen, unit.pause):
                    return
                thought = self._pos
                continue
            if self.settings.autopause_on_focus_change and w.foreground_window() != target:
                self._pause_from_worker(gen)
                log.info("Пауза (сменилось окно → %s) на %d/%d", w.describe_window(w.foreground_window()),
                         self._pos, len(self._units))
                self.message.emit("Пауза: сменилось активное окно. Вернитесь в нужное окно и продолжите.")
                continue
            try:
                self._execute(unit, gen, enter_done=entered == self._pos)
            except OSError as e:
                log.error("Ошибка SendInput на %d/%d: %s", self._pos, len(self._units), e)
                self._pause_from_worker(gen)
                self.message.emit(f"Ошибка ввода: {e}")
                continue
            self._pos += 1
            self.progress.emit(unit.src_end, total)
            if not self._sleep(gen, self._delay_after(unit)):
                return
        if self._wset(gen, FINISHED):
            log.info("Набор завершён: %d единиц", len(self._units))
            self.finished.emit()

    def _wait_line(self, gen: int) -> bool:
        """Печать по строкам: строка набрана — ждём «дальше». False — если печать отменена."""
        if self.settings.profile == PROFILE_IDE:
            # убрать дописанное редактором справа и закрыть подсказки — иначе Enter пользователя
            # примет автодополнение вместо новой строки
            self._clear_right()
        self._line_go.clear()
        self._line_by_enter = False
        if not self._wset(gen, LINE_WAIT):
            return False
        while not self._line_go.wait(0.05):
            if not self._alive(gen):
                return False
        # сразу «Печатает»: защита снова ловит случайные клавиши, пока ждём отпускания Ctrl после хоткея
        return self._wset(gen, RUNNING)

    def _execute(self, u: Unit, gen: int | None = None, enter_done: bool = False) -> None:
        s = self.settings
        ide = s.profile == PROFILE_IDE
        if u.kind == "char":
            if ide and u.text in CLEAR_BEFORE:
                # не полагаемся на «перепечатывание» автоскобок — оно у редакторов разное
                # (CodeMirror 6 ломает тройные кавычки): убираем дописанное справа и вставляем символ
                self._clear_right()
            w.type_char(u.text) if len(u.text) == 1 else self._fast(u.text, gen)
            self.sound.emit("space" if u.text == " " else "key")
        elif u.kind == "fast":
            self.sound.emit("space")
            self._fast(u.text, gen)
        elif u.kind == "tab":
            w.tap(w.VK_TAB)
            self.sound.emit("key")
        elif u.kind == "back":
            w.tap(w.VK_BACK)
            self.sound.emit("key")
        elif u.kind == "nav":
            # печать по шагам: перейти к месту вставки (без звука — так обычно щёлкают мышью)
            vk, mods = NAV_KEYS[u.text]
            w.tap(vk, *mods)
        elif u.kind == "newline":
            if not enter_done:   # печать по строкам: Enter пользователь уже нажал сам
                if ide:
                    self._clear_right()
                    if s.esc_before_enter:
                        w.tap(w.VK_ESCAPE)
                        time.sleep(self._gap())
                w.tap(w.VK_RETURN)
                self.sound.emit("enter")
            if ide:
                time.sleep(self._gap() * 2)  # дать редактору вставить автоотступ
                w.tap(w.VK_HOME, w.VK_SHIFT)
                time.sleep(self._gap())
                w.type_char(" ")
                time.sleep(self._gap())
                w.tap(w.VK_BACK)
                time.sleep(self._gap())
        elif u.kind == "cleanup":
            self._clear_right()

    def _clear_right(self) -> None:
        w.tap(w.VK_END, w.VK_SHIFT)
        time.sleep(self._gap())
        w.type_char(" ")
        time.sleep(self._gap())
        w.tap(w.VK_BACK)
        time.sleep(self._gap())

    def _gap(self) -> float:
        return max(5, self.settings.key_gap_ms) / 1000

    def _fast(self, text: str, gen: int | None = None) -> None:
        for ch in text:
            if gen is not None and not self._alive(gen):
                return   # стоп посреди длинного отступа — дальше не печатаем
            w.type_char(ch)
            time.sleep(self._gap())

    def _delay_after(self, u: Unit) -> float:
        s = self.settings
        base = 60.0 / max(30, s.cpm)
        j = max(0, min(90, s.jitter)) / 100
        if s.human_typing:
            # логнормальный разброс: в основном ровно, изредка заметная заминка; среднее = base·k
            sigma = 0.1 + 0.5 * j
            d = base * u.k * random.lognormvariate(-sigma * sigma / 2, sigma)
        else:
            d = base * random.uniform(1 - j, 1 + j)
        if u.kind == "newline":
            d += s.newline_pause_ms / 1000
        elif u.kind == "char" and u.text in PUNCT:
            d += s.punct_pause_ms / 1000
        elif u.kind == "fast":
            d = base * 0.5
        elif u.kind == "nav":
            d = self._gap() * 1.5   # перемещение — быстрая служебная очередь
        elif u.kind == "cleanup":
            d = 0
        return max(d, self._gap())
