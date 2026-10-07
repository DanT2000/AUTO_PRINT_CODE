"""AutoPrintCode — запуск: python app.py"""
from __future__ import annotations

import signal
import sys
import threading

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from autoprint import APP_NAME
from autoprint.storage import DATA_DIR, Settings, TemplateStore


def main() -> int:
    if sys.platform != "win32":
        print("AutoPrintCode работает только в Windows (использует WinAPI SendInput).")
        return 1
    from autoprint import taskbar
    # своя иконка на панели задач, а не иконка python.exe
    taskbar.set_process_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # программа живёт и в трее (без окон): выходит только явно — из меню или кнопкой «Закрыть»
    app.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    from autoprint import system
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    channel = system.instance_name(DATA_DIR)
    lock = QLockFile(str(DATA_DIR / ".lock"))
    # после обновления старый экземпляр может ещё завершаться — подождём его
    if not lock.tryLock(15000 if "--updated" in sys.argv else 100):
        # уже запущено: просим тот экземпляр показать окно (он мог быть свёрнут в трей). Право вывести окно
        # на передний план есть у нас (запустил пользователь) — отдаём его, иначе Windows лишь мигнёт кнопкой
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        sock = QLocalSocket()
        sock.connectToServer(channel)
        if sock.waitForConnected(1000):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
            return 0
        QMessageBox.information(None, APP_NAME, "AutoPrintCode уже запущен (ищите значок в трее).")
        return 0

    from autoprint.logs import setup_logging
    def on_crash(text: str) -> None:
        # окно можно показать только из GUI-потока; ошибки фоновых потоков остаются в журнале
        if threading.current_thread() is threading.main_thread():
            QMessageBox.critical(None, APP_NAME, f"Внутренняя ошибка: {text}\n\n"
                                                 "Подробности — в журнале (меню логотипа → Журнал работы).")

    setup_logging(on_crash=on_crash)

    try:
        store = TemplateStore()
    except RuntimeError as e:
        QMessageBox.critical(None, APP_NAME, str(e))
        return 1

    from autoprint.ui import logo
    from autoprint.ui.frameless import NativeFrameStyler
    from autoprint.ui.main_window import MainWindow
    from autoprint.ui.theme import theme
    settings = Settings.load()
    theme.setup(app, settings.theme)
    styler = NativeFrameStyler(app)   # системные рамки диалогов — в цветах темы
    app.installEventFilter(styler)
    win = MainWindow(settings, store)
    app.setWindowIcon(win.windowIcon())   # и для диалогов
    icon_file = DATA_DIR / "autoprintcode.ico"
    if logo.save_ico(icon_file):
        win.set_taskbar_icon(str(icon_file))
    # повторный запуск программы присылает сюда «show» — показываем окно
    server = QLocalServer()
    QLocalServer.removeServer(channel)

    def on_second_instance() -> None:
        sock = server.nextPendingConnection()
        if sock is not None:   # содержимое не важно — сам факт подключения значит «покажи окно»
            sock.disconnectFromServer()
            sock.deleteLater()
        win._show_window()

    if server.listen(channel):
        server.newConnection.connect(on_second_instance)
    if "--tray" not in sys.argv:   # автозапуск с Windows — сразу в трей, без окна
        win.show()
    code = app.exec()
    # канал «один экземпляр» и блокировку освобождаем до перезапуска: новая версия стартует, пока этот
    # процесс ещё завершается, и должна сама занять канал (иначе её окно не покажется при повторном запуске)
    server.close()
    lock.unlock()
    if win.relaunch_requested:   # обновление или откат: запустить уже новую версию
        from autoprint import updater
        updater.relaunch()
    return code


if __name__ == "__main__":
    sys.exit(main())
