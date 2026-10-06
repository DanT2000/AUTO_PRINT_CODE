"""Вкладка образца: все блоки образца одной лентой."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..storage import BLOCK_CODE, BLOCK_MARKDOWN, Block, Template
from .blocks import BlockWidget, CodeBlockWidget, MarkdownBlockWidget, make_block_widget


class TemplateView(QWidget):
    changed = Signal()            # содержимое образца изменилось → сохранить
    armed_changed = Signal(str)   # id активного блока кода

    def __init__(self, template: Template) -> None:
        super().__init__()
        self.template = template
        self.widgets: list[BlockWidget] = []
        self._typed: tuple[str, int, int] | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.page = QWidget()
        self.page_lay = QVBoxLayout(self.page)
        self.page_lay.setSpacing(10)
        self.page_lay.addStretch(1)
        self.scroll.setWidget(self.page)
        lay.addWidget(self.scroll, 1)
        add_row = QHBoxLayout()
        b_md = QPushButton("＋ Текст (Markdown)")
        b_md.clicked.connect(lambda: self.add_block(BLOCK_MARKDOWN))
        b_code = QPushButton("＋ Блок кода")
        b_code.clicked.connect(lambda: self.add_block(BLOCK_CODE))
        add_row.addWidget(b_md)
        add_row.addWidget(b_code)
        add_row.addStretch(1)
        lay.addLayout(add_row)

        self._rebuild()

    # ------------------------------------------------------------ блоки
    def _rebuild(self, keep_scroll: bool = False) -> None:
        scroll_pos = self.scroll.verticalScrollBar().value()
        for wdg in self.widgets:
            wdg.setParent(None)
            wdg.deleteLater()
        self.widgets = []
        n = 0
        for b in self.template.blocks:
            if b.type == BLOCK_CODE:
                n += 1
            wdg = make_block_widget(b, n)
            self._wire(wdg)
            self.page_lay.insertWidget(self.page_lay.count() - 1, wdg)
            self.widgets.append(wdg)
        # если в образце ещё нет активного блока — делаем активным первый блок кода
        codes = [w for w in self.widgets if isinstance(w, CodeBlockWidget)]
        if codes and not any(w.block.id == self.template.active_block for w in codes):
            self.arm(codes[0].block.id)
        self._apply_armed()
        self._apply_typed()
        if keep_scroll:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_pos))
        else:
            self.scroll.verticalScrollBar().setValue(0)

    def _wire(self, wdg: BlockWidget) -> None:
        wdg.changed.connect(self.changed)
        wdg.move_requested.connect(self._move_block)
        wdg.delete_requested.connect(self._delete_block)
        wdg.type_requested.connect(self._change_type)
        if isinstance(wdg, CodeBlockWidget):
            wdg.arm_requested.connect(lambda w: self.arm(w.block.id))

    def _renumber(self) -> None:
        n = 0
        for w in self.widgets:
            if isinstance(w, CodeBlockWidget):
                n += 1
                w.set_number(n)

    def add_block(self, kind: str) -> None:
        b = Block(kind, "")
        if kind == BLOCK_CODE:
            last = next((x for x in reversed(self.template.blocks) if x.type == BLOCK_CODE), None)
            if last:
                b.lang = last.lang
        self.template.blocks.append(b)
        self._rebuild(keep_scroll=True)
        wdg = self.widgets[-1]
        if isinstance(wdg, CodeBlockWidget):
            self.arm(b.id)
            wdg.editor.setFocus()
        elif isinstance(wdg, MarkdownBlockWidget):
            wdg.set_editing(True)
        QTimer.singleShot(50, wdg, lambda: self.scroll.ensureWidgetVisible(wdg))
        self.changed.emit()

    def _move_block(self, wdg: BlockWidget, d: int) -> None:
        """Переставляет блок без пересборки страницы — редакторы сохраняют состояние."""
        bl = self.template.blocks
        i = bl.index(wdg.block)
        j = i + d
        if not 0 <= j < len(bl):
            return
        bl[i], bl[j] = bl[j], bl[i]
        self.widgets[i], self.widgets[j] = self.widgets[j], self.widgets[i]
        self.page_lay.removeWidget(wdg)
        self.page_lay.insertWidget(j, wdg)
        self._renumber()
        QTimer.singleShot(30, wdg, lambda: self.scroll.ensureWidgetVisible(wdg, 0, 40))
        self.changed.emit()

    def _change_type(self, wdg: BlockWidget, key: str) -> None:
        """Смена типа: роль Markdown-блока или превращение текст ⇄ код (текст сохраняется)."""
        b = wdg.block
        if key == "code":
            if b.type != BLOCK_CODE:
                last = next((x for x in self.template.blocks if x.type == BLOCK_CODE), None)
                b.lang = last.lang if last else b.lang
            b.type = BLOCK_CODE
        else:
            if b.type == BLOCK_CODE:
                b.sel = []
                if b.id == self.template.active_block:
                    self.template.active_block = ""
            b.type = BLOCK_MARKDOWN
            b.role = key
        self._rebuild(keep_scroll=True)
        self.armed_changed.emit(self.template.active_block)
        self.changed.emit()

    def _delete_block(self, wdg: BlockWidget) -> None:
        if wdg.block.text.strip() and QMessageBox.question(
                self, "Удалить блок", "Удалить блок вместе с содержимым?") != QMessageBox.StandardButton.Yes:
            return
        self.template.blocks.remove(wdg.block)
        if wdg.block.id == self.template.active_block:
            self.template.active_block = ""
        self._rebuild(keep_scroll=True)
        if not self.template.active_block:
            self.armed_changed.emit("")
        self.changed.emit()

    # ------------------------------------------------------------ активный блок / прогресс
    def arm(self, block_id: str) -> None:
        if self.template.active_block != block_id:
            self.template.active_block = block_id
            self.changed.emit()
        self._apply_armed()
        self.armed_changed.emit(block_id)

    def _apply_armed(self) -> None:
        for w in self.widgets:
            if isinstance(w, CodeBlockWidget):
                w.set_armed(w.block.id == self.template.active_block)

    def code_widget(self, block_id: str) -> CodeBlockWidget | None:
        return next((w for w in self.widgets if isinstance(w, CodeBlockWidget) and w.block.id == block_id), None)

    def go_to_block(self, block_id: str) -> None:
        if not self.template.find_block(block_id):
            return
        self.arm(block_id)
        w = self.code_widget(block_id)
        if w:
            QTimer.singleShot(30, w, lambda: self.scroll.ensureWidgetVisible(w, 0, 40))

    def set_typed(self, block_id: str, start: int, end: int) -> None:
        self._typed = (block_id, start, end) if block_id else None
        self._apply_typed()

    def _apply_typed(self) -> None:
        for w in self.widgets:
            if isinstance(w, CodeBlockWidget):
                if self._typed and self._typed[0] == w.block.id:
                    w.editor.set_typed(self._typed[1], self._typed[2])
                else:
                    w.editor.clear_typed()
