"""Иконки веб-версии (web/client/public/icons) — тем же рисунком, что значок Python-версии.

    python tools/make_web_icons.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from autoprint.ui.main_window import make_icon_pixmap  # noqa: E402

OUT = ROOT / "web" / "client" / "public" / "icons"
OUT.mkdir(parents=True, exist_ok=True)
BLUE = "#3b82f6"
for size in (192, 512):
    make_icon_pixmap(BLUE, size).save(str(OUT / f"icon-{size}.png"))

# maskable: Windows/Android могут обрезать край — рисунок в безопасной зоне 80 % на фоне
pm = QPixmap(512, 512)
pm.fill(QColor("#1e2229"))
p = QPainter(pm)
p.drawPixmap(51, 51, make_icon_pixmap(BLUE, 410))
p.end()
pm.save(str(OUT / "maskable-512.png"))
print("ok:", sorted(f.name for f in OUT.iterdir()))
