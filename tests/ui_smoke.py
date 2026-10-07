"""Проверка интерфейса без участия человека: собирает главное окно и окно настроек в обеих темах
на временной папке данных и сохраняет снимки.

    python tests/ui_smoke.py <папка для снимков>

Окна не появляются на экране (WA_DontShowOnScreen). Рабочая папка data/ не используется.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AUTOPRINT_DATA"] = tempfile.mkdtemp(prefix="autoprint-ui-")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="autoprint-shots-"))
OUT.mkdir(parents=True, exist_ok=True)


def settle(app: QApplication, n: int = 6) -> None:
    for _ in range(n):
        app.processEvents()


def shot(w, name: str) -> None:
    QApplication.processEvents()
    w.grab().save(str(OUT / f"{name}.png"))
    print("снимок:", OUT / f"{name}.png")


def main() -> int:
    app = QApplication(sys.argv)
    from autoprint.storage import Settings, TemplateStore
    from autoprint.typer import RUNNING
    from autoprint.ui.main_window import MainWindow
    from autoprint.ui.settings_window import PAGES
    from autoprint.ui.theme import theme

    settings = Settings()
    theme.setup(app, "dark")
    store = TemplateStore()
    win = MainWindow(settings, store)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(1320, 840)
    win.show()
    settle(app)
    shot(win, "main-dark")

    # печать «идёт»: ход в пульте, подсветка в блоке
    job = win._job_for_armed()
    assert job, "в примере должен быть активный блок кода"
    win.job = job
    win._target = "VS Code"
    win._last_pos = job["text"].index("result +=") if "result +=" in job["text"] else 20
    win.strip.set_state(RUNNING)
    win._show_job_progress()
    settle(app)
    shot(win, "main-dark-running")
    win.job = None
    win._clear_progress()
    win._on_state()   # пульт — снова по настоящему состоянию движка («Готов»), а не «Печатает» из снимка
    win._update_armed_label()

    # пошаговый урок: режим «По шагам», без комментариев — суфлёр и цветные шаги
    steps_t = next(t for t in store.templates if t.title.startswith("Пример: пошаговый"))
    win.open_template(steps_t.id)
    win.strip.sw_strip.setChecked(True)
    win._on_mode("steps")
    settle(app)
    shot(win, "main-steps-dark")
    code_id = steps_t.code_blocks()[0].id
    win._set_step_pointer(code_id, 3)   # шаг 3 — возврат наверх
    settle(app)
    shot(win, "main-steps3-dark")
    win._on_mode("lines")
    settle(app)
    shot(win, "main-lines-dark")
    win._on_mode("block")
    win.strip.sw_strip.setChecked(False)
    win.open_template(store.templates[0].id)
    settle(app)

    settings.theme = "light"
    theme.set_mode("light")
    settle(app)
    shot(win, "main-light")

    win.resize(1000, 700)
    settle(app)
    shot(win, "main-light-narrow")
    win.resize(1320, 840)

    settings.theme = "dark"
    theme.set_mode("dark")
    win.open_settings()
    sw = win.settings_win
    sw.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    sw.show()
    settle(app)
    for key, _ic, _t in PAGES:
        sw.show_page(key)
        settle(app)
        shot(sw, f"settings-{key}-dark")
    settings.theme = "light"
    theme.set_mode("light")
    sw.show_page("human")
    settle(app)
    shot(sw, "settings-human-light")

    # всплывающее меню логотипа
    settings.theme = "dark"
    theme.set_mode("dark")
    win._fill_logo_menu()
    win.logo_menu.adjustSize()
    shot(win.logo_menu, "logo-menu-dark")
    sw.close()
    win.close()
    settle(app)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
