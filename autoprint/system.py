"""Связь с Windows: значок у часов, автозапуск, один экземпляр программы.

Значок у часов. Windows 11 прячет новые значки трея в «скрытые значки» (стрелка ^). Какие значки видны
всегда, хранится в реестре: HKCU\\Control Panel\\NotifyIconSettings\\<номер>, параметр IsPromoted = 1.
Подраздел для нашего значка Windows создаёт сама, когда значок впервые показан, — поэтому после показа
несколько секунд ждём его появления (так же делает NoVPN). Подраздел находится по пути exe процесса;
Windows записывает пути из Program Files через GUID известной папки — их разворачиваем.

Автозапуск — параметр в HKCU\\…\\Run: программа стартует сразу в трей (--tray).
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from .storage import app_dir

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AutoPrintCode"
NOTIFY_ROOT = r"Control Panel\NotifyIconSettings"

_KNOWN_FOLDERS = {
    "{6D809377-6AF0-444B-8957-A3773F02200E}": os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", ""),
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": os.environ.get("ProgramFiles(x86)", ""),
    "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": os.environ.get("SystemRoot", r"C:\Windows"),
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
}

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE


def process_image() -> str:
    """Полный путь exe нашего процесса — тот, что Windows пишет в настройки значков трея.
    (Из venv программу запускает базовый pythonw.exe, а не тот, что лежит в .venv.)"""
    buf = ctypes.create_unicode_buffer(1024)
    n = wintypes.DWORD(len(buf))
    if _kernel32.QueryFullProcessImageNameW(_kernel32.GetCurrentProcess(), 0, buf, ctypes.byref(n)):
        return buf.value
    return sys.executable


def _expand(path: str) -> str:
    for guid, folder in _KNOWN_FOLDERS.items():
        if path.upper().startswith(guid) and folder:
            return folder + path[len(guid):]
    return path


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(_expand(path)))


def _promote_once(exe: str, on: bool) -> bool:
    """Выставить IsPromoted нашим записям. True — если хоть одна нашлась."""
    want = _norm(exe)
    found = False
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ROOT)
    except OSError:
        return False
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as sub:
                    path, _t = winreg.QueryValueEx(sub, "ExecutablePath")
                    if _norm(str(path)) != want:
                        continue
                    found = True
                    try:
                        cur, _t = winreg.QueryValueEx(sub, "IsPromoted")
                    except OSError:
                        cur = None
                    if cur != (1 if on else 0):
                        winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, 1 if on else 0)
            except OSError:
                continue
    return found


def pin_tray_icon(on: bool, wait_s: float = 9.0) -> None:
    """Показывать значок у часов (on) или вернуть его в «скрытые значки». Работает в фоне:
    запись в реестре появляется только после первого показа значка."""
    exe = process_image()

    def work() -> None:
        end = time.monotonic() + wait_s
        time.sleep(0.8)
        while True:
            if _promote_once(exe, on):
                log.info("Значок трея %s: %s", "закреплён у часов" if on else "в скрытых значках", exe)
                return
            if time.monotonic() > end:
                log.info("Запись значка трея ещё не создана Windows — закрепим при следующем запуске")
                return
            time.sleep(0.5)

    threading.Thread(target=work, name="tray-pin", daemon=True).start()


def launch_command(tray: bool = True) -> str:
    """Команда запуска программы (для автозапуска): exe или pythonw + app.py."""
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        exe = Path(sys.executable)
        gui = exe.with_name("pythonw.exe")
        cmd = f'"{gui if gui.exists() else exe}" "{app_dir() / "app.py"}"'
    return cmd + (" --tray" if tray else "")


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> bool:
    """Включить/выключить автозапуск. Путь обновляется при каждом запуске (программу могли перенести)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, launch_command(tray=True))
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log.warning("Автозапуск не изменён: %s", e)
        return False


def instance_name(data_dir: Path) -> str:
    """Имя канала «один экземпляр» — своё для каждой папки данных (как и файл блокировки)."""
    import hashlib
    h = hashlib.sha1(str(data_dir.resolve()).lower().encode()).hexdigest()[:12]
    return f"autoprintcode-{h}"
