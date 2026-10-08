"""Снимки окна для README: docs/images/*.png.

    python tools/make_screenshots.py

Окна собираются вне экрана (WA_DontShowOnScreen) на временной папке данных со встроенными примерами —
в снимки не попадают ни ваши образцы, ни настройки. Масштаб 1.5 — чтобы на GitHub картинки были чёткими.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AUTOPRINT_DATA"] = tempfile.mkdtemp(prefix="autoprint-shots-")
os.environ["QT_SCALE_FACTOR"] = "1.5"
# настоящая отрисовка Windows (шрифты как в программе), но окна не выводятся на экран (WA_DontShowOnScreen),
# а значок в трее, уведомления и глобальные хоткеи отключает tests/quiet.py
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QEvent, QObject, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
app = QApplication(sys.argv)
sys.path.insert(0, str(ROOT / "tests"))
import quiet  # noqa: E402 — без значка в трее, уведомлений и глобальных хоткеев

quiet.patch()


class _OffScreen(QObject):
    """Каждому окну — WA_DontShowOnScreen ещё до первого показа: снимок делается, на экране ничего нет."""

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.Polish and isinstance(obj, QWidget) and obj.isWindow():
            obj.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        return False


_offscreen = _OffScreen()
app.installEventFilter(_offscreen)


def settle(n: int = 8) -> None:
    for _ in range(n):
        app.processEvents()


def shot(w, name: str) -> None:
    settle()
    for t in (getattr(w, "toast", None), getattr(getattr(w, "settings_win", None), "toast", None)):
        if t is not None:   # всплывающие подсказки о смене режима и т. п. в снимок не нужны
            t.timer.stop()
            t.anim.stop()
            t.hide()
    settle()
    path = OUT / f"{name}.png"
    w.grab().save(str(path), "PNG", 9)
    print(f"{path.name}  {path.stat().st_size // 1024} КБ")


def scroll_to(win, block_id: str) -> None:
    """Прокрутить ленту так, чтобы блок был вверху."""
    view = win.current_view()
    settle()
    wdg = next(w for w in view.widgets if w.block.id == block_id)
    view.scroll.verticalScrollBar().setValue(max(0, wdg.mapTo(view.scroll.widget(), wdg.rect().topLeft()).y() - 12))
    settle()


def logo_images() -> None:
    from autoprint.ui import logo
    for size in (128, 256):
        logo.pixmap(logo.APP, size).save(str(OUT / f"logo-{size}.png"), "PNG", 9)
    # значок в трее: готов / печатает / пауза — на тёмной и светлой панели задач
    cell, pad = 64, 22
    states = (("Готов", logo.IDLE), ("Печатает", logo.RUNNING), ("Пауза", logo.PAUSED))
    w, h = len(states) * (cell + 2 * pad) + 40, 2 * (cell + 2 * pad) + 30
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont("Segoe UI", 11)
    p.setFont(f)
    for row, (bg, fg) in enumerate((("#202024", "#E8E8EA"), ("#F3F3F5", "#2B2B30"))):
        y = row * (cell + 2 * pad + 6)
        p.setBrush(QColor(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, y, w, cell + 2 * pad, 14, 14)
        for i, (name, kind) in enumerate(states):
            x = 20 + i * (cell + 2 * pad)
            p.drawPixmap(x + pad, y + 8, logo.pixmap(kind, cell - 16))
            p.setPen(QColor(fg))
            p.drawText(QRect(x, y + cell - 6, cell + 2 * pad, 26), Qt.AlignmentFlag.AlignHCenter, name)
            p.setPen(Qt.PenStyle.NoPen)
    p.end()
    pm.save(str(OUT / "tray-states.png"), "PNG", 9)
    print("docs/images/logo-*.png, tray-states.png")


def main() -> int:
    from autoprint.samples import TOUR_TITLE
    from autoprint.storage import Settings, TemplateStore
    from autoprint.typer import RUNNING
    from autoprint.ui.main_window import MainWindow
    from autoprint.ui.theme import theme

    logo_images()
    s = Settings()
    s.update_auto_check = False
    s.sound_enabled = True
    theme.setup(app, "dark")
    store = TemplateStore()
    win = MainWindow(s, store)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(1240, 800)
    win.show()
    tour = next(t for t in store.templates if t.title == TOUR_TITLE)
    win.open_template(tour.id)
    settle()
    shot(win, "main-dark")

    # идёт печать: ход на пульте, напечатанное в блоке ярче остального (только вид — защита не включается)
    job = win._job_for_armed()
    win.job = job
    win._target = "Code.exe"
    win._last_pos = job["text"].index("return result")
    win.engine.state = RUNNING
    win.strip.set_state(RUNNING)
    win._show_job_progress()
    win._set_title_status()
    scroll_to(win, tour.blocks[1].id)
    shot(win, "printing")
    win.engine.state = "idle"
    win.job = None
    win._clear_progress()
    win._on_state()
    win._update_armed_label()

    # урок по шагам: суфлёр — что сказать перед шагом, слева у строк — номера шагов
    steps_t = next(t for t in store.templates if t.title.startswith("Пример: пошаговый"))
    win.open_template(steps_t.id)
    win.strip.sw_strip.setChecked(True)
    win._on_mode("steps")
    win._set_step_pointer(steps_t.code_blocks()[0].id, 3)
    shot(win, "steps")
    win.strip.sw_strip.setChecked(False)
    win._on_mode("lines")
    win.open_template(tour.id)
    view = win.current_view()
    lines_block = tour.code_blocks()[1]
    view.arm(lines_block.id)
    scroll_to(win, tour.blocks[4].id)
    shot(win, "lines")
    view.arm(tour.code_blocks()[0].id)
    view.scroll.verticalScrollBar().setValue(0)
    win._on_mode("block")
    win.strip.sw_strip.setChecked(False)
    win.open_template(tour.id)

    theme.set_mode("light")
    s.theme = "light"
    shot(win, "main-light")
    theme.set_mode("dark")
    s.theme = "dark"

    win.open_settings()
    sw = win.settings_win
    sw.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    sw.resize(1000, 720)
    sw.show()
    for key in ("print", "human", "sound", "look"):
        sw.show_page(key)
        shot(sw, f"settings-{key}")
    sw.close()

    win._fill_logo_menu()
    win.logo_menu.adjustSize()
    shot(win.logo_menu, "logo-menu")

    from autoprint.ui.report_dialog import ReportDialog
    d = ReportDialog(win, s)
    d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    d.with_settings.setChecked(False)   # без названий звуковых устройств этого компьютера
    d.desc.setPlainText("Печатал урок по шагам в VS Code — после шага 3 строка вставилась не туда.")
    d._recollect(False)
    d.resize(860, 720)
    d.show()
    shot(d, "report")
    d.close()

    win._quitting = True
    win.close()
    settle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
