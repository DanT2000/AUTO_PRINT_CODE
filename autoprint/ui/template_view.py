"""Вкладка образца: слева список задач, справа блоки выбранной задачи."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
                               QToolButton, QVBoxLayout, QWidget)

from ..storage import BLOCK_CODE, BLOCK_MARKDOWN, Block, Task, Template, new_task
from .blocks import BlockWidget, CodeBlockWidget, MarkdownBlockWidget, make_block_widget


class TemplateView(QWidget):
    changed = Signal()            # содержимое образца изменилось → сохранить
    armed_changed = Signal(str)   # id активного блока кода

    def __init__(self, template: Template) -> None:
        super().__init__()
        self.template = template
        self.widgets: list[BlockWidget] = []
        self._typed: tuple[str, int, int] | None = None
        self._loading = False

        split = QSplitter(Qt.Orientation.Horizontal)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        # ---- задачи
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>Задачи</b>"))
        self.tasks = QListWidget()
        self.tasks.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tasks.currentItemChanged.connect(self._on_task_selected)
        self.tasks.itemChanged.connect(self._on_task_renamed)
        ll.addWidget(self.tasks, 1)
        row = QHBoxLayout()
        for text, tip, slot in (("＋", "Добавить задачу", self.add_task),
                                ("↑", "Задачу выше", lambda: self.move_task(-1)),
                                ("↓", "Задачу ниже", lambda: self.move_task(+1)),
                                ("✕", "Удалить задачу", self.delete_task)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        ll.addLayout(row)
        split.addWidget(left)

        # ---- блоки
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.page = QWidget()
        self.page_lay = QVBoxLayout(self.page)
        self.page_lay.setSpacing(10)
        self.page_lay.addStretch(1)
        self.scroll.setWidget(self.page)
        rl.addWidget(self.scroll, 1)
        add_row = QHBoxLayout()
        b_md = QPushButton("＋ Текст (Markdown)")
        b_md.clicked.connect(lambda: self.add_block(BLOCK_MARKDOWN))
        b_code = QPushButton("＋ Блок кода")
        b_code.clicked.connect(lambda: self.add_block(BLOCK_CODE))
        add_row.addWidget(b_md)
        add_row.addWidget(b_code)
        add_row.addStretch(1)
        rl.addLayout(add_row)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 800])

        self._fill_tasks()

    # ------------------------------------------------------------ задачи
    def _fill_tasks(self, select_id: str = "") -> None:
        self._loading = True
        self.tasks.clear()
        for t in self.template.tasks:
            it = QListWidgetItem(t.title)
            it.setData(Qt.ItemDataRole.UserRole, t.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.tasks.addItem(it)
        self._loading = False
        target = select_id or self.template.active_task
        row = next((i for i, t in enumerate(self.template.tasks) if t.id == target), 0)
        if self.template.tasks:
            self.tasks.setCurrentRow(row)
        else:
            self._show_task(None)

    def current_task(self):
        it = self.tasks.currentItem()
        return self.template.task(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _on_task_selected(self, cur, _prev) -> None:
        if self._loading:
            return
        task = self.template.task(cur.data(Qt.ItemDataRole.UserRole)) if cur else None
        self._show_task(task)

    def _on_task_renamed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        task = self.template.task(item.data(Qt.ItemDataRole.UserRole))
        if task and item.text().strip() and task.title != item.text().strip():
            task.title = item.text().strip()
            self.changed.emit()

    def add_task(self) -> None:
        n = len(self.template.tasks) + 1
        title, ok = QInputDialog.getText(self, "Новая задача", "Название:", text=f"Задача {n}")
        if not ok:
            return
        task = new_task(title.strip() or f"Задача {n}")
        cur = self.current_task()
        idx = self.template.tasks.index(cur) + 1 if cur else len(self.template.tasks)
        self.template.tasks.insert(idx, task)
        self._fill_tasks(task.id)
        self.changed.emit()

    def move_task(self, d: int) -> None:
        cur = self.current_task()
        if not cur:
            return
        ts = self.template.tasks
        i = ts.index(cur)
        j = i + d
        if 0 <= j < len(ts):
            ts[i], ts[j] = ts[j], ts[i]
            self._fill_tasks(cur.id)
            self.changed.emit()

    def delete_task(self) -> None:
        cur = self.current_task()
        if not cur:
            return
        if QMessageBox.question(self, "Удалить задачу", f"Удалить задачу «{cur.title}»?") \
                != QMessageBox.StandardButton.Yes:
            return
        i = self.template.tasks.index(cur)
        self.template.tasks.remove(cur)
        if any(b.id == self.template.active_block for b in cur.blocks):
            self.template.active_block = ""
            self.armed_changed.emit("")
        nxt = self.template.tasks[min(i, len(self.template.tasks) - 1)].id if self.template.tasks else ""
        self._fill_tasks(nxt)
        self.changed.emit()

    # ------------------------------------------------------------ блоки
    def _show_task(self, task, keep_scroll: bool = False) -> None:
        scroll_pos = self.scroll.verticalScrollBar().value()
        for wdg in self.widgets:
            wdg.setParent(None)
            wdg.deleteLater()
        self.widgets = []
        if task is None:
            return
        if self.template.active_task != task.id:
            self.template.active_task = task.id
            self.changed.emit()
        n = 0
        for b in task.blocks:
            if b.type == BLOCK_CODE:
                n += 1
            wdg = make_block_widget(b, n)
            self._wire(wdg)
            self.page_lay.insertWidget(self.page_lay.count() - 1, wdg)
            self.widgets.append(wdg)
        # если в задаче ещё нет активного блока — делаем активным первый блок кода
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
        wdg.move_to_task_requested.connect(self._move_to_task)
        if isinstance(wdg, CodeBlockWidget):
            wdg.arm_requested.connect(lambda w: self.arm(w.block.id))

    def _renumber(self) -> None:
        n = 0
        for w in self.widgets:
            if isinstance(w, CodeBlockWidget):
                n += 1
                w.set_number(n)

    def add_block(self, kind: str) -> None:
        task = self.current_task()
        if not task:
            return
        b = Block(kind, "")
        if kind == BLOCK_CODE:
            last = next((x for x in reversed(task.blocks) if x.type == BLOCK_CODE), None)
            if last:
                b.lang = last.lang
        task.blocks.append(b)
        self._show_task(task)
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
        task = self.current_task()
        bl = task.blocks
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
                task = self.current_task()
                last = next((x for x in task.blocks if x.type == BLOCK_CODE), None)
                b.lang = last.lang if last else b.lang
            b.type = BLOCK_CODE
        else:
            if b.type == BLOCK_CODE:
                b.sel = []
                if b.id == self.template.active_block:
                    self.template.active_block = ""
            b.type = BLOCK_MARKDOWN
            b.role = key
        self._show_task(self.current_task(), keep_scroll=True)
        self.armed_changed.emit(self.template.active_block)
        self.changed.emit()

    def _move_to_task(self, wdg: BlockWidget) -> None:
        cur = self.current_task()
        menu = QMenu(self)
        menu.addSection("Перенести блок в задачу")
        for t in self.template.tasks:
            if t is cur:
                continue
            menu.addAction(t.title, lambda t=t: self._do_move_to_task(wdg.block, t))
        menu.addSeparator()
        menu.addAction("＋ Новая задача…", lambda: self._do_move_to_task(wdg.block, None))
        menu.exec(QCursor.pos())

    def _do_move_to_task(self, block: Block, target) -> None:
        cur = self.current_task()
        if target is None:
            n = len(self.template.tasks) + 1
            title, ok = QInputDialog.getText(self, "Новая задача", "Название:", text=f"Задача {n}")
            if not ok:
                return
            target = Task(title=title.strip() or f"Задача {n}")
            self.template.tasks.insert(self.template.tasks.index(cur) + 1, target)
        cur.blocks.remove(block)
        target.blocks.append(block)
        if block.id == self.template.active_block:
            self.template.active_block = ""
        self._fill_tasks(cur.id)
        self.armed_changed.emit(self.template.active_block)
        self.changed.emit()

    def _delete_block(self, wdg: BlockWidget) -> None:
        if wdg.block.text.strip() and QMessageBox.question(
                self, "Удалить блок", "Удалить блок вместе с содержимым?") != QMessageBox.StandardButton.Yes:
            return
        task = self.current_task()
        task.blocks.remove(wdg.block)
        if wdg.block.id == self.template.active_block:
            self.template.active_block = ""
        self._show_task(task)
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
        found = self.template.find_block(block_id)
        if not found:
            return
        task, _ = found
        cur = self.current_task()
        if not cur or cur.id != task.id:
            row = self.template.tasks.index(task)
            self.template.active_block = block_id
            self.tasks.setCurrentRow(row)
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

    def refresh_titles(self) -> None:
        self._fill_tasks(self.template.active_task)
