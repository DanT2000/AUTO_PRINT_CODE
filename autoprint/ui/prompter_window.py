"""Окно суфлёра: маленькое, поверх всех окон, фокус у редактора не забирает.

Урок без возни с кодом: преподаватель читает, что сказать, видит кусок кода и жмёт «Дальше» — кнопку
здесь или хоткей (тот же, что старт печати). Два режима:
- «Печатает программа» — «Дальше» печатает шаг в редактор;
- «Печатаю сам» — программа ничего не печатает: «Дальше» листает шаги, код набирает сам преподаватель,
  а суфлёр показывает, что набрать и куда.
Окно не становится активным (WS_EX_NOACTIVATE): щелчок по «Дальше» не уводит фокус из редактора.
Что показывать — решает PrompterController (prompter_control.py), окно только рисует.
"""
from __future__ import annotations

import ctypes
import html
from dataclasses import dataclass, field

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

from .frameless import FramelessWindow, user32
from .theme import theme
from .widgets import IconLabel, Segmented, accent_button, button, label

MODE_AUTO, MODE_SELF = "auto", "self"
MODES = [(MODE_AUTO, "Печатает программа"), (MODE_SELF, "Печатаю сам")]

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3

FONT_MIN, FONT_MAX = 14, 36


@dataclass
class PromptCard:
    """Что показать: заголовок, что сказать, куски кода (где — подпись, строки), кнопки."""
    title: str = ""
    sub: str = ""                                      # блок, занятие
    say: list[str] = field(default_factory=list)
    chunks: list[tuple[str, list[str]]] = field(default_factory=list)
    hidden: int = 0                                    # строк кода не показано
    hint: str = ""
    next_text: str = "Дальше"
    next_icon: str = "next"
    can_next: bool = True
    can_back: bool = True
    empty: str = ""                                    # вместо содержимого — пояснение


class PrompterWindow(FramelessWindow):
    next_pressed = Signal()
    next_clicked = Signal()
    back_clicked = Signal()
    mode_changed = Signal(str)
    font_changed = Signal(int)
    geometry_changed = Signal(str)

    def __init__(self, mode: str, font_px: int) -> None:
        super().__init__("Суфлёр", minimize=False, maximize=False)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("prompterWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(320, 240)
        self.resize(460, 380)
        self.font_px = max(FONT_MIN, min(FONT_MAX, font_px))
        tb = self.titlebar
        tb.left.addWidget(IconLabel("lines", "accent", 16))
        self.btn_smaller = self._text_button("A−", "Текст мельче", -2)
        self.btn_bigger = self._text_button("A+", "Текст крупнее", +2)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(14, 6, 14, 12)
        lay.setSpacing(8)
        self.mode = Segmented(MODES)
        self.mode.set_value(mode if mode in (MODE_AUTO, MODE_SELF) else MODE_AUTO)
        self.mode.changed.connect(lambda k: self.mode_changed.emit(str(k)))
        self.mode.setToolTip("Печатает программа — «Дальше» печатает шаг в редактор.\n"
                             "Печатаю сам — «Дальше» только листает шаги: код набираете вы, глядя сюда.")
        for b in self.mode.buttons.values():
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.mode)
        self.sub = label("", "pwSub")
        self.sub.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.sub)

        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 4, 0)
        il.setSpacing(10)
        self.say = QLabel()
        self.say.setObjectName("pwSay")
        self.say.setWordWrap(True)
        self.say.setTextFormat(Qt.TextFormat.PlainText)    # комментарии из кода — не HTML
        self.say.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        il.addWidget(self.say)
        self.code = QLabel()
        self.code.setObjectName("pwCode")
        self.code.setTextFormat(Qt.TextFormat.RichText)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        il.addWidget(self.code)
        self.empty = label("", "pwEmpty", wrap=True)
        il.addWidget(self.empty)
        il.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("pwScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_back = button("←  Назад", quiet=True)
        self.btn_back.setToolTip("Шаг назад (ничего не печатает и не стирает)")
        self.btn_back.clicked.connect(self.back_clicked)
        self.hint = label("", "pwHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_next = accent_button("Дальше", "next")
        self.btn_next.setMinimumWidth(130)
        self.btn_next.pressed.connect(self.next_pressed)
        self.btn_next.clicked.connect(self.next_clicked)
        for b in (self.btn_back, self.btn_next):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bottom.addWidget(self.btn_back)
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(self.btn_next)
        lay.addLayout(bottom)

        self._geo_timer = QTimer(self)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.setInterval(500)
        self._geo_timer.timeout.connect(
            lambda: self.geometry_changed.emit(bytes(self.saveGeometry().toBase64()).decode()))
        self._card = PromptCard()
        theme.changed.connect(self._restyle)
        self._restyle()

    # ------------------------------------------------------------ вид
    def _text_button(self, text: str, tip: str, d: int) -> QToolButton:
        b = QToolButton()
        b.setObjectName("iconBtn")
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(34, 30)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(lambda: self._zoom(d))
        self.titlebar.right.addWidget(b)
        return b

    def _restyle(self) -> None:
        t = theme
        f = self.font_px
        self.setStyleSheet(f"""
QWidget#prompterWindow {{ background: {t.css('bg')}; }}
QLabel#pwSub {{ color: {t.css('dim')}; font-size: 12px; }}
QLabel#pwSay {{ font-family: "{t.display_family}"; font-size: {f}px; color: {t.css('text')}; }}
QLabel#pwCode {{ font-family: "{t.mono_family}"; font-size: {max(11, f - 6)}px; color: {t.css('text')};
    background: {t.css('editor_bg')}; border: 1px solid {t.css('stroke')}; border-radius: 6px; padding: 8px 10px; }}
QLabel#pwEmpty {{ color: {t.css('dim')}; font-size: 13px; }}
QLabel#pwHint {{ color: {t.css('faint')}; font-size: 11px; }}
QToolButton#iconBtn {{ color: {t.css('dim')}; font-size: 13px; font-weight: 600; }}
QToolButton#iconBtn:hover {{ color: {t.css('text')}; }}
QScrollArea#pwScroll, QScrollArea#pwScroll > QWidget > QWidget {{ background: transparent; }}
""")
        self.show_card(self._card)

    def _zoom(self, d: int) -> None:
        n = max(FONT_MIN, min(FONT_MAX, self.font_px + d))
        if n != self.font_px:
            self.font_px = n
            self._restyle()
            self.font_changed.emit(n)

    def set_mode(self, mode: str) -> None:
        self.mode.set_value(mode)

    def show_card(self, c: PromptCard) -> None:
        self._card = c
        self.titlebar.set_status(c.title)
        self.sub.setText(c.sub)
        self.sub.setVisible(bool(c.sub))
        self.say.setText("\n".join(c.say))
        self.say.setVisible(bool(c.say))
        self.code.setText(self._code_html(c))
        self.code.setVisible(bool(c.chunks))
        self.empty.setText(c.empty)
        self.empty.setVisible(bool(c.empty))
        self.hint.setText(c.hint)
        self.btn_next.setText(f" {c.next_text}")
        self.btn_next._icon_name = c.next_icon
        self.btn_next._refresh()
        self.btn_next.setEnabled(c.can_next)
        self.btn_back.setEnabled(c.can_back)

    @staticmethod
    def _code_html(c: PromptCard) -> str:
        faint = theme.css("faint")
        out = []
        for where, rows in c.chunks:
            if where:
                out.append(f"<span style='color:{faint}'>{html.escape(where)}</span>")
            out.extend(html.escape(r) if r else "&nbsp;" for r in rows)
        if c.hidden:
            out.append(f"<span style='color:{faint}'>… и ещё строк: {c.hidden}</span>")
        return "<div style='white-space:pre'>" + "<br>".join(out) + "</div>"

    # ------------------------------------------------------------ окно
    def restore(self, geometry_b64: str) -> None:
        if geometry_b64:
            self.restoreGeometry(QByteArray.fromBase64(geometry_b64.encode()))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        hwnd = int(self.winId())
        try:
            ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if not ex & WS_EX_NOACTIVATE:
                user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
        except (AttributeError, OSError, ctypes.ArgumentError):
            pass

    def nativeEvent(self, event_type, message):
        try:
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_MOUSEACTIVATE:
                return True, MA_NOACTIVATE     # щелчок не делает окно активным: фокус остаётся в редакторе
        except (TypeError, ValueError):
            pass
        return super().nativeEvent(event_type, message)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        if self.isVisible():
            self._geo_timer.start()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.isVisible():
            self._geo_timer.start()
