"""Библиотека образцов слева — как меню разделов PasteTalk."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QListWidget, QPushButton,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import icons
from .theme import theme
from .widgets import accent_button, button, label

ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_COUNT = Qt.ItemDataRole.UserRole + 1


class LibraryDelegate(QStyledItemDelegate):
    ROW_H = 34

    def paint(self, p: QPainter, opt, index) -> None:
        r = QRectF(opt.rect).adjusted(0, 1, 0, -1)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if selected or hover:
            p.setBrush(theme.c("card") if selected else theme.c("stroke"))
            p.drawRoundedRect(r, 5, 5)
        if selected:
            p.setBrush(theme.c("accent"))
            p.drawRoundedRect(QRectF(r.left(), r.center().y() - 8, 3, 16), 1.5, 1.5)
        ic = icons.pixmap("file-code", theme.c("accent" if selected else "dim"), 15, 1.8)
        p.drawPixmap(int(r.left() + 12), int(r.center().y() - 7.5), ic)
        count = str(index.data(ROLE_COUNT) or "")
        right = int(r.right() - 10)
        if count:
            f = theme.mono(11)
            p.setFont(f)
            p.setPen(theme.c("faint"))
            cw = QFontMetrics(f).horizontalAdvance(count)
            p.drawText(QRect(right - cw, int(r.top()), cw, int(r.height())),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count)
            right -= cw + 8
        f = theme.font(13)
        p.setFont(f)
        p.setPen(theme.c("text"))
        left = int(r.left() + 36)
        text = QFontMetrics(f).elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "",
                                          Qt.TextElideMode.ElideRight, right - left)
        p.drawText(QRect(left, int(r.top()), right - left, int(r.height())),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.restore()

    def sizeHint(self, opt, index) -> QSize:
        return QSize(opt.rect.width(), self.ROW_H)

    def updateEditorGeometry(self, editor, opt, index) -> None:
        editor.setGeometry(opt.rect.adjusted(32, 3, -6, -3))


class LibraryPanel(QWidget):
    """Поиск, список образцов, «Новый» и «Импорт». Объявление об обновлении — над кнопками."""
    new_requested = Signal()
    import_requested = Signal()
    update_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("navPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(244)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(6)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Поиск образцов…")
        self.search.setClearButtonEnabled(True)
        self._search_action = self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        lay.addWidget(self.search)
        lay.addSpacing(4)
        lay.addWidget(label("Образцы", "navGroup"))

        self.list = QListWidget()
        self.list.setObjectName("library")
        self.list.setItemDelegate(LibraryDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.list, 1)

        self.update_btn = QPushButton()
        self.update_btn.setObjectName("updateBtn")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.update_clicked)
        self.update_btn.hide()
        lay.addWidget(self.update_btn)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_new = accent_button("Новый", "plus")
        self.btn_new.setToolTip("Новый образец (Ctrl+N)")
        self.btn_new.clicked.connect(self.new_requested)
        self.btn_import = button("Импорт", "import")
        self.btn_import.setToolTip("Импорт из .ipynb (Jupyter), .md, .py или .json (Ctrl+O)")
        self.btn_import.clicked.connect(self.import_requested)
        row.addWidget(self.btn_new, 1)
        row.addWidget(self.btn_import, 1)
        lay.addLayout(row)
        theme.changed.connect(self._on_theme)

    def _on_theme(self) -> None:
        self._search_action.setIcon(icons.icon("search"))
        self.update_btn.setIcon(icons.icon("upgrade", "accent"))
        self.list.viewport().update()

    def show_update(self, text: str | None) -> None:
        if text:
            self.update_btn.setText(text)
            self.update_btn.setIcon(icons.icon("upgrade", "accent"))
            self.update_btn.show()
        else:
            self.update_btn.hide()
