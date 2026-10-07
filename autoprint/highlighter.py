"""Подсветка синтаксиса для редактора образца (Pygments → QSyntaxHighlighter)."""
from __future__ import annotations

import weakref
from bisect import bisect_right

import shiboken6
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.token import Comment, Keyword, Name, Number, Operator, String, Token
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

LANGUAGES = ["python", "javascript", "typescript", "html", "css", "sql", "java", "c", "cpp",
             "csharp", "go", "rust", "php", "bash", "powershell", "json", "yaml", "markdown", "text"]

# Цвета подсветки под тему оформления (тёплый янтарь PasteTalk + спокойные холодные тона)
PALETTES = {
    "dark": [
        (Comment, "#6E6E7A", False, True),
        (Keyword, "#F0B54A", False, False),
        (Name.Builtin, "#6FC3DF", False, False),
        (Name.Function, "#7FB4FF", False, False),
        (Name.Class, "#F2CC60", True, False),
        (Name.Decorator, "#F2CC60", False, False),
        (Name.Tag, "#F2686C", False, False),
        (Name.Attribute, "#E8A87C", False, False),
        (String, "#9CD67C", False, False),
        (Number, "#D2A8FF", False, False),
        (Operator, "#A6A6B2", False, False),
    ],
    "light": [
        (Comment, "#8A8A96", False, True),
        (Keyword, "#A35F00", False, False),
        (Name.Builtin, "#0F7A99", False, False),
        (Name.Function, "#1F5FBF", False, False),
        (Name.Class, "#8A5A00", True, False),
        (Name.Decorator, "#8A5A00", False, False),
        (Name.Tag, "#C0272D", False, False),
        (Name.Attribute, "#A0522D", False, False),
        (String, "#237A32", False, False),
        (Number, "#7A3DB8", False, False),
        (Operator, "#5A5A66", False, False),
    ],
}


def _fmt(color: str, bold: bool, italic: bool) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


_FORMATS: list = []
_live: "weakref.WeakSet[CodeHighlighter]" = weakref.WeakSet()


def set_palette(name: str) -> None:
    """Сменить цвета подсветки во всех открытых редакторах (при смене темы)."""
    global _FORMATS
    _FORMATS = [(tt, _fmt(c, b, i)) for tt, c, b, i in PALETTES.get(name, PALETTES["dark"])]
    for h in list(_live):
        # редактор удалён (блок убран, образец закрыт), а обёртка Python ещё жива — пропускаем
        doc = h.document() if shiboken6.isValid(h) else None
        if doc is None or not shiboken6.isValid(doc):
            _live.discard(h)
            continue
        h._refresh()


set_palette("dark")


def _format_for(ttype) -> QTextCharFormat | None:
    while ttype is not Token:
        for tt, f in _FORMATS:
            if ttype is tt:
                return f
        ttype = ttype.parent
    return None


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document, lang: str = "python") -> None:
        super().__init__(document)
        _live.add(self)
        self._starts: list[int] = []
        self._spans: list[tuple[int, int, QTextCharFormat]] = []
        self._lexer = TextLexer()
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._refresh)
        document.contentsChange.connect(self._on_change)
        self.set_language(lang)

    def set_language(self, lang: str) -> None:
        try:
            self._lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
        except Exception:
            self._lexer = TextLexer()
        self._refresh()

    def _on_change(self, _pos, removed, added):
        if not self._busy and (removed or added):
            self._timer.start()

    def _refresh(self) -> None:
        self._relex()
        self._busy = True
        try:
            self.rehighlight()
        finally:
            self._busy = False

    def _relex(self) -> None:
        text = self.document().toPlainText()
        spans = []
        pos = 0
        for ttype, value in lex(text, self._lexer):
            f = _format_for(ttype)
            if f is not None and value:
                spans.append((pos, pos + len(value), f))
            pos += len(value)
        self._spans = spans
        self._starts = [s for s, _, _ in spans]

    def highlightBlock(self, text: str) -> None:
        start = self.currentBlock().position()
        end = start + len(text)
        i = max(0, bisect_right(self._starts, start) - 1)
        while i < len(self._spans):
            s, e, f = self._spans[i]
            if s >= end:
                break
            if e > start:
                a, b = max(s, start), min(e, end)
                self.setFormat(a - start, b - a, f)
            i += 1
