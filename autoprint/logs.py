"""Журнал работы: data/logs/autoprint.log (ротация 3 × 1 МБ).

Пишутся только служебные события: старт/пауза/стоп, причина, целевое окно,
ошибки. Набираемый текст и нажатия пользователя в журнал НЕ попадают.
"""
from __future__ import annotations

import logging
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__
from .storage import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "autoprint.log"

log = logging.getLogger("autoprint")


def setup_logging(on_crash=None) -> None:
    """on_crash(text) — вызывается при необработанном исключении (например, показать окно)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:  # pythonw — без консоли
        root.addHandler(logging.StreamHandler())

    def excepthook(exc_type, exc, tb):
        log.critical("Необработанное исключение", exc_info=(exc_type, exc, tb))
        if on_crash:
            try:
                on_crash(f"{exc_type.__name__}: {exc}")
            except Exception:
                pass

    sys.excepthook = excepthook
    threading.excepthook = lambda a: excepthook(a.exc_type, a.exc_value, a.exc_traceback)

    try:
        from PySide6 import __version__ as qt_ver
    except Exception:
        qt_ver = "?"
    log.info("==== %s %s запущен · Python %s · PySide6 %s · %s %s · frozen=%s",
             APP_NAME, __version__, platform.python_version(), qt_ver,
             platform.system(), platform.version(), getattr(sys, "frozen", False))
