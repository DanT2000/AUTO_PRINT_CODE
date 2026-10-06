"""AutoPrintCode — запуск: python app.py"""
from __future__ import annotations

import ctypes
import signal
import sys
import threading

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from autoprint import APP_NAME, __version__
from autoprint.storage import DATA_DIR, Settings, TemplateStore


def main() -> int:
    if sys.platform != "win32":
        print("AutoPrintCode работает только в Windows (использует WinAPI SendInput).")
        return 1
    # своя иконка на панели задач, а не иконка python.exe
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"autoprintcode.{__version__}")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(DATA_DIR / ".lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "AutoPrintCode уже запущен (ищите значок в трее).")
        return 0

    from autoprint.logs import setup_logging
    def on_crash(text: str) -> None:
        # окно можно показать только из GUI-потока; ошибки фоновых потоков остаются в журнале
        if threading.current_thread() is threading.main_thread():
            QMessageBox.critical(None, APP_NAME, f"Внутренняя ошибка: {text}\n\n"
                                                 "Подробности — в журнале (Файл → Открыть журнал работы).")

    setup_logging(on_crash=on_crash)

    try:
        store = TemplateStore()
    except RuntimeError as e:
        QMessageBox.critical(None, APP_NAME, str(e))
        return 1

    from autoprint.ui.main_window import MainWindow
    win = MainWindow(Settings.load(), store)
    win.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
