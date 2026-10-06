"""Виджеты блоков образца: текстовый (Markdown) и код."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QSizePolicy, QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout)

from ..highlighter import LANGUAGES
from ..storage import BLOCK_CODE, ROLES, Block
from .code_editor import CodeEditor, code_font

ARMED_COLOR = "#2ea043"


def _tool(text: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    return b


CODE_TYPE = "code"
TYPE_ITEMS = [(f"{icon} {name}", key) for key, (icon, name, _c) in ROLES.items()] + [("💻 Код", CODE_TYPE)]


class BlockWidget(QFrame):
    changed = Signal()
    move_requested = Signal(object, int)          # (виджет, -1|+1)
    delete_requested = Signal(object)
    type_requested = Signal(object, str)          # (виджет, роль | "code")

    def __init__(self, block: Block, code_number: int = 0) -> None:
        super().__init__()
        self.block = block
        self.code_number = code_number
        self.setObjectName("block")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)

        # шапка: [тип][заголовок][доп. слева] … [доп. справа][↑][↓][⋯][✕]
        self.header = QHBoxLayout()
        self.header.setSpacing(4)
        self.kind = QComboBox()
        self.kind.setToolTip("Тип блока")
        for label, key in TYPE_ITEMS:
            self.kind.addItem(label, key)
        self.kind.setCurrentIndex(self.kind.findData(CODE_TYPE if block.type == BLOCK_CODE else block.role))
        self.kind.activated.connect(self._on_kind)
        self.header.addWidget(self.kind)

        self.title = QLineEdit(block.title)
        self.title.setFrame(False)
        self.title.setToolTip("Заголовок блока — щёлкните, чтобы изменить")
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.title.setMinimumWidth(120)
        self.title.setMaximumWidth(320)
        self.title.setStyleSheet("QLineEdit { background: transparent; }")
        self.title.textEdited.connect(self._on_title)
        self.header.addWidget(self.title)
        self._update_placeholder()

        self.left_slot = QHBoxLayout()
        self.header.addLayout(self.left_slot)
        self.header.addStretch(1)
        self.right_slot = QHBoxLayout()
        self.header.addLayout(self.right_slot)

        for text, tip, slot in (("↑", "Блок выше", lambda: self.move_requested.emit(self, -1)),
                                ("↓", "Блок ниже", lambda: self.move_requested.emit(self, +1)),
                                ("✕", "Удалить блок", lambda: self.delete_requested.emit(self))):
            b = _tool(text, tip)
            b.clicked.connect(slot)
            self.header.addWidget(b)
        lay.addLayout(self.header)
        self.body = QVBoxLayout()
        lay.addLayout(self.body)
        self._apply_role_style()

    def _update_placeholder(self) -> None:
        self.title.setPlaceholderText(Block(self.block.type, role=self.block.role, title="")
                                      .display_title(self.code_number))

    def _on_title(self, text: str) -> None:
        self.block.title = text
        self.changed.emit()

    def _on_kind(self, _i: int) -> None:
        key = self.kind.currentData()
        current = CODE_TYPE if self.block.type == BLOCK_CODE else self.block.role
        if key != current:
            self.type_requested.emit(self, key)

    def _apply_role_style(self) -> None:
        if self.block.type != BLOCK_CODE:
            color = ROLES.get(self.block.role, ROLES["text"])[2]
            self.setStyleSheet(f"QFrame#block {{ border-left: 4px solid {color}; }}")


# ------------------------------------------------------------------ Markdown

class _AutoBrowser(QTextBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(4)

    def fit(self) -> None:
        self.document().setTextWidth(self.viewport().width())
        self.setFixedHeight(max(30, int(self.document().size().height()) + 6))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.fit()


class MarkdownBlockWidget(BlockWidget):
    edit_requested = Signal()

    def __init__(self, block: Block) -> None:
        super().__init__(block)
        self.toggle = _tool("✎ Править", "Переключить: просмотр / редактирование Markdown")
        self.toggle.clicked.connect(self.toggle_mode)
        self.right_slot.addWidget(self.toggle)

        self.stack = QStackedWidget()
        self.view = _AutoBrowser()
        self.view.viewport().installEventFilter(self)
        self.edit = QPlainTextEdit(block.text)
        self.edit.setFont(code_font(10))
        self.edit.setPlaceholderText("Текст в формате Markdown: условие, пояснение к коду, подсказка…")
        self.edit.textChanged.connect(self._on_text)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.edit)
        self.body.addWidget(self.stack)
        self._render()
        self.set_editing(not block.text.strip())

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == ev.Type.MouseButtonDblClick:
            self.set_editing(True)
            return True
        return False

    def _on_text(self) -> None:
        self.block.text = self.edit.toPlainText()
        self._fit_edit()
        self.changed.emit()

    def _render(self) -> None:
        self.view.setMarkdown(self.block.text or "*Пустой блок — дважды щёлкните, чтобы написать*")
        QTimer.singleShot(0, self, self._fit)

    def _fit(self) -> None:
        if self.stack.currentWidget() is self.view:
            self.view.fit()
            self.stack.setFixedHeight(self.view.height())
        else:
            self._fit_edit()

    def _fit_edit(self) -> None:
        if self.stack.currentWidget() is self.edit:
            lines = max(4, min(25, self.edit.document().blockCount() + 1))
            h = lines * self.edit.fontMetrics().lineSpacing() + 16
            self.edit.setFixedHeight(h)
            self.stack.setFixedHeight(h)

    def set_editing(self, on: bool) -> None:
        if on:
            self.stack.setCurrentWidget(self.edit)
            self.toggle.setText("✔ Готово")
            self.edit.setFocus()
        else:
            self._render()
            self.stack.setCurrentWidget(self.view)
            self.toggle.setText("✎ Править")
        self._fit()

    def toggle_mode(self) -> None:
        self.set_editing(self.stack.currentWidget() is self.view)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        QTimer.singleShot(0, self, self._fit)


# ------------------------------------------------------------------ Код

class CodeBlockWidget(BlockWidget):
    arm_requested = Signal(object)
    selection_changed = Signal(object)

    def __init__(self, block: Block, number: int) -> None:
        super().__init__(block, number)
        self.armed = False
        self.arm_btn = _tool("▶ Печатать этот", "Сделать этот блок активным для печати по хоткею")
        self.arm_btn.setCheckable(True)
        self.arm_btn.clicked.connect(lambda: self.arm_requested.emit(self))
        self.left_slot.addWidget(self.arm_btn)
        self.info = QLabel()
        self.info.setStyleSheet("color: gray;")
        self.left_slot.addWidget(self.info)
        self.lang = QComboBox()
        self.lang.setEditable(True)
        self.lang.addItems(LANGUAGES)
        self.lang.setCurrentText(block.lang)
        self.lang.setToolTip("Язык подсветки (на печать не влияет)")
        self.lang.currentTextChanged.connect(self._on_lang)
        self.right_slot.addWidget(self.lang)

        self.editor = CodeEditor(block.text, block.lang)
        self.editor.setPlaceholderText("Код образца. Выделите строки — будут напечатаны только они.")
        self.editor.textChanged.connect(self._on_text)
        self.editor.selectionChanged.connect(self._on_selection)
        self.editor.focused.connect(lambda: self.arm_requested.emit(self))
        self.editor.height_changed.connect(self._fit)
        self.body.addWidget(self.editor)
        self._restore_selection()
        self._fit()
        self._update_info()

    def set_number(self, n: int) -> None:
        self.code_number = n
        self._update_placeholder()

    def _fit(self) -> None:
        self.editor.setFixedHeight(self.editor.content_height())

    def _on_lang(self, lang: str) -> None:
        self.block.lang = lang.strip().lower() or "text"
        self.editor.highlighter.set_language(self.block.lang)
        self.changed.emit()

    def _on_text(self) -> None:
        self.block.text = self.editor.toPlainText()
        self._update_info()
        self.changed.emit()

    def _on_selection(self) -> None:
        c = self.editor.textCursor()
        self.block.sel = [c.selectionStart(), c.selectionEnd()] if c.hasSelection() else []
        self._update_info()
        self.selection_changed.emit(self)
        self.changed.emit()

    def _restore_selection(self) -> None:
        if len(self.block.sel) == 2:
            n = len(self.block.text)
            a, b = (min(max(0, x), n) for x in self.block.sel)
            if b > a:
                c = self.editor.textCursor()
                c.setPosition(a)
                c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
                self.editor.setTextCursor(c)

    def clear_selection(self) -> None:
        c = self.editor.textCursor()
        c.clearSelection()
        self.editor.setTextCursor(c)

    def _update_info(self) -> None:
        text = self.block.text
        lines = text.count("\n") + 1 if text else 0
        if self.block.sel:
            a, b = self.block.sel
            n = text[a:b].rstrip("\n").count("\n") + 1
            self.info.setText(f"· выделено строк: {n} из {lines}")
            self.info.setStyleSheet("color: #d29922; font-weight: bold;")
        else:
            self.info.setText(f"· {lines} стр., {len(text)} симв.")
            self.info.setStyleSheet("color: gray;")

    def set_armed(self, on: bool) -> None:
        self.armed = on
        self.arm_btn.setChecked(on)
        self.arm_btn.setText("● Активный" if on else "▶ Печатать этот")
        self.setStyleSheet(f"QFrame#block {{ border: 2px solid {ARMED_COLOR}; border-radius: 6px; }}"
                           if on else "")

    def typing_text(self, whole_lines: bool) -> tuple[str, int]:
        return typing_slice(self.block.text, self.block.sel, whole_lines)


def typing_slice(text: str, sel: list[int], whole_lines: bool) -> tuple[str, int]:
    """Что печатать: выделение (расширенное до целых строк) или весь блок. → (текст, смещение)."""
    if len(sel) != 2 or sel[1] <= sel[0]:
        return text, 0
    a, b = max(0, sel[0]), min(len(text), sel[1])
    if whole_lines:
        a = text.rfind("\n", 0, a) + 1
        if b > a and text[b - 1] == "\n":
            b -= 1
        nl = text.find("\n", b)
        b = len(text) if nl < 0 else nl
    return text[a:b], a


def make_block_widget(block: Block, code_number: int) -> BlockWidget:
    if block.type == BLOCK_CODE:
        return CodeBlockWidget(block, code_number)
    return MarkdownBlockWidget(block)
