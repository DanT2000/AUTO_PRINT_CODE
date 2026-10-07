"""Суфлёр под пультом: что будет напечатано дальше и что сказать перед этим.

Для записи урока: окно AutoPrintCode — вне кадра (второй монитор или захват только окна редактора),
ведущий читает здесь комментарии шага (с «Без комментариев» они не печатаются) и жмёт хоткей.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from .widgets import IconLabel, label


class Prompter(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 13)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = IconLabel("lines", "accent", 16)
        head.addWidget(self.icon)
        self.title = label("", "prompterTitle")
        head.addWidget(self.title, 1)
        self.hint = label("", "faint")
        head.addWidget(self.hint)
        lay.addLayout(head)
        self.say = QLabel()
        self.say.setObjectName("prompterSay")
        self.say.setWordWrap(True)
        self.say.setTextFormat(Qt.TextFormat.PlainText)   # комментарии из кода — не HTML
        self.say.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.say)
        self.code = QLabel()
        self.code.setObjectName("prompterCode")
        self.code.setTextFormat(Qt.TextFormat.PlainText)
        self.code.setWordWrap(False)
        lay.addWidget(self.code)
        self.hide()

    def show_content(self, title: str, hint: str, say: list[str], code: list[str], hidden: int = 0) -> None:
        self.title.setText(title)
        self.hint.setText(hint)
        self.say.setText("\n".join(say))
        self.say.setVisible(bool(say))
        lines = list(code)
        if hidden:
            lines.append(f"… и ещё строк: {hidden}")
        self.code.setText("\n".join(lines))
        self.code.setVisible(bool(lines))
        self.show()
