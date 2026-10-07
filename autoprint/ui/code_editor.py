"""Редактор кода образца: номера строк, подсветка синтаксиса, ход печати, разметка шагов.

Шаг строки хранится в данных строки документа (QTextBlockUserData) — так он «едет» вместе со строкой
при правке текста. Новая строка получает шаг строки, на которой нажали Enter.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QTextBlockUserData, QTextCursor, QTextFormat
from PySide6.QtWidgets import QMenu, QPlainTextEdit, QTextEdit, QWidget

from ..highlighter import CodeHighlighter
from .theme import theme

# цвета шагов (тёмная / светлая тема) — по кругу
STEP_COLORS = {
    True: ["#E9A72C", "#7FB4FF", "#6FD08C", "#B28CFF", "#F2866C", "#6FC3DF", "#F27FB4", "#C3D96F"],
    False: ["#B87400", "#1F5FBF", "#237A32", "#7A3DB8", "#C0472D", "#0F7A99", "#B8336A", "#6E7F12"],
}
STEP_COL = 26   # ширина колонки шагов в поле номеров строк


def step_color(step: int) -> QColor:
    pal = STEP_COLORS[theme.dark]
    return QColor(pal[(max(1, step) - 1) % len(pal)])


class _StepData(QTextBlockUserData):
    def __init__(self, step: int) -> None:
        super().__init__()
        self.step = step


def code_font(size: int = 11) -> QFont:
    families = set(QFontDatabase.families())
    for name in (theme.mono_family, "Cascadia Mono", "JetBrains Mono", "Cascadia Code", "Consolas", "Courier New"):
        if name in families:
            f = QFont(name, size)
            break
    else:
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


class _Gutter(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_gutter(event)

    def mousePressEvent(self, e) -> None:
        self.editor.gutter_clicked(e)

    def mouseMoveEvent(self, e) -> None:
        on_step = self.editor.steps_visible and e.position().x() < STEP_COL
        self.setCursor(Qt.CursorShape.PointingHandCursor if on_step else Qt.CursorShape.ArrowCursor)
        self.setToolTip("Шаг строки — щёлкните, чтобы изменить" if on_step else "")


class CodeEditor(QPlainTextEdit):
    height_changed = Signal()
    focused = Signal()
    steps_changed = Signal()            # шаги строк поменялись (разметка или правка текста)
    markup_requested = Signal(str)      # авторазметка: "comments" | "blank" | "reset"

    MIN_LINES = 3
    MAX_LINES = 40

    def __init__(self, text: str = "", lang: str = "python", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("codeEditor")
        self.setFont(code_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabChangesFocus(False)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(6)
        self.setPlainText(text)
        self.highlighter = CodeHighlighter(self.document(), lang)
        self._gutter = _Gutter(self)
        self._progress: tuple[int, int, int] | None = None   # (начало, где печать, конец) в символах блока
        self.steps_visible = False
        self._step_view: tuple[int, int | None] | None = None   # (шаг, который печатается/следующий, позиция)
        self._steps_cache: list[int] = []
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._refresh_extra)
        self.blockCountChanged.connect(lambda *_: self.height_changed.emit())
        self.textChanged.connect(self._sync_steps)
        theme.changed.connect(self._refresh_extra)
        self.set_steps([])
        self._update_gutter_width()
        self._refresh_extra()

    # ---- высота по содержимому (блоки идут в общей прокрутке страницы)
    def content_height(self) -> int:
        lines = max(self.MIN_LINES, min(self.MAX_LINES, self.document().blockCount()))
        sb = self.horizontalScrollBar().sizeHint().height()   # место под прокрутку длинных строк
        return int(lines * self.fontMetrics().lineSpacing() + 2 * self.document().documentMargin() + sb + 2)

    # ---- номера строк и колонка шагов
    def gutter_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits + (STEP_COL if self.steps_visible else 0)

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def update_gutter_width(self) -> None:
        """После смены шрифта (масштаб): ширина колонки номеров строк и её геометрия."""
        self._update_gutter_width()
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event) -> None:
        p = QPainter(self._gutter)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.font())
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        sel = self._selected_lines()
        cur = self.textCursor().blockNumber() if self.hasFocus() else -1
        gutter, accent, fg = theme.c("editor_gutter"), theme.c("accent"), theme.c("dim")
        small = QFont(self.font())
        small.setPointSizeF(max(6.0, self.font().pointSizeF() * 0.78))
        lh = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if self.steps_visible:
                    step = self._block_step(block)
                    c = step_color(step)
                    chip = QRectF(4, top + 2, STEP_COL - 8, lh - 4)
                    bg = QColor(c)
                    bg.setAlphaF(0.22)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(bg)
                    p.drawRoundedRect(chip, 3, 3)
                    p.setPen(c)
                    p.setFont(small)
                    p.drawText(chip, Qt.AlignmentFlag.AlignCenter, str(step))
                    p.setFont(self.font())
                in_sel = sel and sel[0] <= num <= sel[1]
                p.setPen(accent if in_sel else fg if num == cur else gutter)
                p.drawText(0, top, self._gutter.width() - 10, lh, Qt.AlignmentFlag.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    def _selected_lines(self) -> tuple[int, int] | None:
        c = self.textCursor()
        if not c.hasSelection():
            return None
        doc = self.document()
        a = doc.findBlock(c.selectionStart()).blockNumber()
        end = c.selectionEnd()
        if end > c.selectionStart() and doc.findBlock(end).position() == end:
            end -= 1   # выделение до начала строки эту строку не захватывает
        b = doc.findBlock(end).blockNumber()
        return a, b

    # ---- шаги строк
    @staticmethod
    def _block_step(block) -> int:
        d = block.userData()
        return d.step if isinstance(d, _StepData) else 1

    def set_steps(self, steps: list[int]) -> None:
        """Шаги строк из образца (пусто — все строки шага 1)."""
        b = self.document().firstBlock()
        i = 0
        last = 1
        while b.isValid():
            last = max(1, int(steps[i])) if i < len(steps) else last
            b.setUserData(_StepData(last))
            b = b.next()
            i += 1
        self._steps_cache = self.line_steps()
        self._gutter.update()
        self._refresh_extra()

    def line_steps(self) -> list[int]:
        out = []
        b = self.document().firstBlock()
        while b.isValid():
            out.append(self._block_step(b))
            b = b.next()
        return out

    def set_steps_visible(self, on: bool) -> None:
        if on != self.steps_visible:
            self.steps_visible = on
            self.update_gutter_width()
            self._gutter.update()

    def _sync_steps(self) -> None:
        """Новые строки (Enter, вставка) получают шаг строки выше."""
        b = self.document().firstBlock()
        prev = None
        while b.isValid():
            if not isinstance(b.userData(), _StepData):
                step = prev
                if step is None:   # первая строка без шага — как у следующей размеченной
                    nb = b.next()
                    while nb.isValid() and not isinstance(nb.userData(), _StepData):
                        nb = nb.next()
                    step = self._block_step(nb) if nb.isValid() else 1
                b.setUserData(_StepData(step))
            prev = self._block_step(b)
            b = b.next()
        steps = self.line_steps()
        if steps != self._steps_cache:
            self._steps_cache = steps
            self.steps_changed.emit()

    def assign_step(self, first: int, last: int, step: int) -> None:
        doc = self.document()
        for n in range(first, last + 1):
            b = doc.findBlockByNumber(n)
            if b.isValid():
                b.setUserData(_StepData(max(1, step)))
        self._steps_cache = self.line_steps()
        self._gutter.update()
        self._refresh_extra()
        self.steps_changed.emit()

    def _target_lines(self) -> tuple[int, int]:
        sel = self._selected_lines()
        if sel:
            return sel
        n = self.textCursor().blockNumber()
        return n, n

    def step_menu(self, first: int, last: int, parent=None) -> QMenu:
        """Меню «шаг для строк first…last»: существующие шаги и «новый»."""
        m = QMenu(parent or self)
        existing = sorted(set(self.line_steps()))
        top = max(existing) if existing else 1
        what = f"строки {first + 1}" if first == last else f"строк {first + 1}–{last + 1}"
        head = m.addAction(f"Шаг для {what}")
        head.setEnabled(False)
        for k in range(1, top + 2):
            a = m.addAction(f"Шаг {k}" + ("  (новый)" if k == top + 1 else "") + (f"\tAlt+{k}" if k <= 9 else ""))
            a.triggered.connect(lambda _=False, k=k: self.assign_step(first, last, k))
        m.addSeparator()
        for key, text in (("comments", "Разметить по комментариям"), ("blank", "Разметить по пустым строкам"),
                          ("reset", "Сбросить: весь блок — шаг 1")):
            m.addAction(text, lambda key=key: self.markup_requested.emit(key))
        return m

    def gutter_clicked(self, e) -> None:
        if not self.steps_visible or e.position().x() >= STEP_COL:
            return
        y = e.position().y()
        b = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(b).translated(self.contentOffset()).top()
        while b.isValid():
            h = self.blockBoundingRect(b).height()
            if top <= y < top + h:
                n = b.blockNumber()
                sel = self._selected_lines()
                first, last = sel if sel and sel[0] <= n <= sel[1] else (n, n)
                self.step_menu(first, last).exec(self._gutter.mapToGlobal(e.position().toPoint()))
                return
            top += h
            b = b.next()

    def contextMenuEvent(self, e) -> None:
        m = self.createStandardContextMenu()
        m.addSeparator()
        first, last = self._target_lines()
        sub = self.step_menu(first, last, m)
        sub.setTitle("Шаг строк (печать по шагам)")
        m.addMenu(sub)
        m.exec(e.globalPos())

    # ---- ход печати: напечатанное — как есть, ненапечатанное — приглушено, текущее место отмечено
    def set_progress(self, start: int, pos: int, end: int) -> None:
        n = self.document().characterCount() - 1
        start, end = max(0, min(start, n)), max(0, min(end, n))
        pos = max(start, min(pos, end))
        if self._progress != (start, pos, end):
            self._progress = (start, pos, end)
            self._refresh_extra()

    def clear_progress(self) -> None:
        if self._progress is not None:
            self._progress = None
            self._refresh_extra()

    def set_step_view(self, step: int | None, pos: int | None = None) -> None:
        """Печать по шагам: step — шаг, который печатается или следующий; pos — где печать (None — ждём).
        Строки следующих шагов приглушены, строки этого шага подсвечены цветом шага."""
        view = (step, pos) if step is not None else None
        if view != self._step_view:
            self._step_view = view
            self._refresh_extra()

    # ---- подсветка текущей строки, хода печати и шагов
    def _refresh_extra(self) -> None:
        extras = []
        if self._step_view is not None:
            extras += self._step_extras(*self._step_view)
        if self.hasFocus() and not self.textCursor().hasSelection() and self._progress is None:
            s = QTextEdit.ExtraSelection()
            s.format.setBackground(theme.c("editor_line"))
            s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            s.cursor = self.textCursor()
            s.cursor.clearSelection()
            extras.append(s)
        if self._progress is not None:
            start, pos, end = self._progress
            if pos < end:
                extras += self._range(pos, end, fg=theme.c("editor_todo"))
                extras += self._range(pos, pos + 1, bg=theme.c("editor_mark"), fg=theme.c("editor_fg"))
        self.setExtraSelections(extras)
        self._gutter.update()

    def _range(self, a: int, b: int, fg: QColor | None = None, bg: QColor | None = None,
               full: bool = False) -> list:
        s = QTextEdit.ExtraSelection()
        if fg is not None:
            s.format.setForeground(fg)
        if bg is not None:
            s.format.setBackground(bg)
        if full:
            s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        c = QTextCursor(self.document())
        c.setPosition(a)
        c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        s.cursor = c
        return [s]

    def _step_extras(self, step: int, pos: int | None) -> list:
        out = []
        tint = step_color(step)
        tint.setAlphaF(0.10)
        b = self.document().firstBlock()
        while b.isValid():
            s = self._block_step(b)
            a, e = b.position(), b.position() + b.length() - 1
            if s > step:
                out += self._range(a, e, fg=theme.c("editor_todo"))          # ещё не в программе
            elif s == step:
                if pos is None:
                    c = QTextCursor(b)
                    sel = QTextEdit.ExtraSelection()
                    sel.format.setBackground(tint)
                    sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                    sel.cursor = c
                    out.append(sel)
                elif pos <= e:
                    out += self._range(max(a, pos), e, fg=theme.c("editor_todo"))
                    if a <= pos <= e:
                        out += self._range(pos, min(pos + 1, e + 1), bg=theme.c("editor_mark"),
                                           fg=theme.c("editor_fg"))
            b = b.next()
        return out

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self._refresh_extra()
        self.focused.emit()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._refresh_extra()

    # ---- Tab → пробелы, Shift+Tab — снять отступ, Alt+цифра — шаг строк
    def keyPressEvent(self, e) -> None:
        mods = e.modifiers()
        if mods == Qt.KeyboardModifier.AltModifier and Qt.Key.Key_0 <= e.key() <= Qt.Key.Key_9:
            first, last = self._target_lines()
            k = e.key() - Qt.Key.Key_0
            if k == 0:   # Alt+0 — новый шаг
                k = max(self.line_steps() or [1]) + 1
            self.assign_step(first, last, k)
            self.set_steps_visible(True)
            return
        if e.key() == Qt.Key.Key_Tab and not mods:
            c = self.textCursor()
            if c.hasSelection():
                self._indent_selection(+1)
            else:
                c.insertText("    ")
            return
        if e.key() == Qt.Key.Key_Backtab:
            self._indent_selection(-1)
            return
        plain = mods & ~Qt.KeyboardModifier.KeypadModifier   # Enter цифрового блока — тоже Enter
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain in (Qt.KeyboardModifier.NoModifier,
                                                                           Qt.KeyboardModifier.ShiftModifier):
            # Shift+Enter в QPlainTextEdit — «мягкий» перенос (U+2028) внутри той же строки документа:
            # в тексте блока он становится «\n», и номера строк, шаги и подсветка хода печати разъезжаются
            line = self.textCursor().block().text()
            indent = line[:len(line) - len(line.lstrip(" "))]
            if line.rstrip().endswith((":", "{", "[", "(")):
                indent += "    "
            self.textCursor().insertText("\n" + indent)
            return
        super().keyPressEvent(e)

    def insertFromMimeData(self, source) -> None:
        if source.hasText():
            # «мягкие» переносы из браузера/Word (U+2028) — обычными строками, по той же причине, что Shift+Enter
            text = source.text().replace(" ", "\n").replace(" ", "\n")
            self.textCursor().insertText(text)
            self.ensureCursorVisible()
            return
        super().insertFromMimeData(source)

    def _indent_selection(self, direction: int) -> None:
        c = self.textCursor()
        doc = self.document()
        first = doc.findBlock(c.selectionStart())
        end = c.selectionEnd()
        if c.hasSelection() and end > c.selectionStart() and doc.findBlock(end).position() == end:
            end -= 1  # выделение до начала строки не захватывает эту строку
        last = doc.findBlock(end)
        c.beginEditBlock()
        b = first
        while b.isValid():
            bc = QTextCursor(b)
            if direction > 0:
                bc.insertText("    ")
            else:
                n = len(b.text()) - len(b.text().lstrip(" "))
                bc.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, min(4, n))
                bc.removeSelectedText()
            if b == last:
                break
            b = b.next()
        c.endEditBlock()
