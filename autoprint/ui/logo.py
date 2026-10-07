"""Логотип: строки кода с курсором. Значок приложения — на графитовой плитке, в трее — без плитки.

Состояния значка в трее: «Готов» — контур строк, «Печатает» — залитая плитка (видно издалека),
«Пауза» — строки и две черты.
"""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QLineF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

ACCENT = QColor("#E9A72C")
PLATE = QColor("#1B1B1F")

APP, IDLE, RUNNING, PAUSED = "app", "idle", "running", "paused"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

# состояние движка → вид значка
STATE_KIND = {"idle": IDLE, "finished": IDLE, "countdown": RUNNING, "running": RUNNING, "paused": PAUSED,
              "line_wait": RUNNING}


def _lines(p: QPainter, color: QColor, width: float, lines) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for x1, y1, x2, y2 in lines:
        p.drawLine(QLineF(x1, y1, x2, y2))
    p.setPen(Qt.PenStyle.NoPen)


def paint(p: QPainter, size: float, kind: str, accent: QColor = ACCENT) -> None:
    """Рисует знак в квадрат size×size (координаты знака — 64×64)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    if kind == APP:
        p.setBrush(PLATE)
        p.drawRoundedRect(QRectF(0, 0, 64, 64), 14, 14)
        _lines(p, accent, 4.5, ((14, 20, 50, 20), (14, 30, 40, 30), (22, 40, 34, 40)))
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(38, 35, 5, 11), 1.5, 1.5)
    elif kind == RUNNING:
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(2, 4, 60, 56), 12, 12)
        _lines(p, PLATE, 6, ((13, 18, 51, 18), (13, 32, 39, 32), (21, 46, 32, 46)))
        p.setBrush(PLATE)
        p.drawRoundedRect(QRectF(37, 40, 6, 13), 2, 2)
    elif kind == PAUSED:
        _lines(p, accent, 7, ((8, 16, 56, 16), (8, 32, 42, 32), (24, 44, 24, 56), (38, 44, 38, 56)))
    else:   # IDLE — знак без плитки
        _lines(p, accent, 7, ((8, 16, 56, 16), (8, 32, 42, 32), (20, 48, 34, 48)))
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(40, 40, 7, 16), 2, 2)
    p.restore()


def pixmap(kind: str, size: int, accent: QColor = ACCENT, dpr: float = 1.0) -> QPixmap:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint(p, pm.width(), kind, accent)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def make_icon(kind: str, accent: QColor = ACCENT) -> QIcon:
    ic = QIcon()
    for s in ICON_SIZES:
        ic.addPixmap(pixmap(kind, s, accent))
    return ic


def save_ico(path: Path, kind: str = APP) -> bool:
    """Многоразмерный .ico (PNG внутри) — его Windows показывает на панели задач."""
    images = []
    for s in ICON_SIZES:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap(kind, s).save(buf, "PNG")
        images.append((s, bytes(buf.data())))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = len(head) + 16 * len(images)
    entries = b""
    for s, data in images:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    try:
        path.write_bytes(head + entries + b"".join(d for _s, d in images))
        return True
    except OSError:
        return False
