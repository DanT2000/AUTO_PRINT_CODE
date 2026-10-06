"""Защита набора от случайного вмешательства пользователя.

Пока идёт печать, ставятся низкоуровневые хуки клавиатуры и мыши (WH_KEYBOARD_LL,
WH_MOUSE_LL). Физическое нажатие клавиши или кнопки мыши → сигнал tripped,
движок встаёт на паузу. Нажатая клавиша поглощается, чтобы не попасть в код.
Свои синтетические события (флаг INJECTED) пропускаются. Хуки снимаются сразу
после паузы/остановки — вне печати приложение клавиатуру не слушает.
Ничего не записывается: проверяется только факт нажатия.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN = 0x0201, 0x0204, 0x0207, 0x020B
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
WM_QUIT = 0x0012
WM_APP_ARM = 0x8000 + 10
WM_APP_DISARM = 0x8000 + 11

# Модификаторы сами по себе паузу не вызывают: ими начинается хоткей (Ctrl+F9 и т.п.).
_MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x14, 0x90, 0x91}
_MOD_VK = ((0x0002, 0x11), (0x0001, 0x12), (0x0004, 0x10), (0x0008, 0x5B), (0x0008, 0x5C))

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class InputGuard(QObject):
    tripped = Signal(str)   # "key" | "mouse"

    def __init__(self) -> None:
        super().__init__()
        self.enabled = True
        self._hotkeys: set[tuple[int, int]] = set()
        self._armed = False
        self._fired = False
        self._kb = None
        self._ms = None
        self._thread_id = 0
        self._ready = threading.Event()
        # ссылки на колбэки держим, иначе сборщик мусора их удалит → падение процесса
        self._kb_proc = HOOKPROC(self._on_key)
        self._ms_proc = HOOKPROC(self._on_mouse)
        self._thread = threading.Thread(target=self._run, name="input-guard", daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def set_hotkeys(self, hotkeys: set[tuple[int, int]]) -> None:
        """(модификаторы MOD_*, vk) зарегистрированных хоткеев — их нажатие не считается вмешательством."""
        self._hotkeys = set(hotkeys)

    def arm(self) -> None:
        if self.enabled:
            user32.PostThreadMessageW(self._thread_id, WM_APP_ARM, 0, 0)

    def disarm(self) -> None:
        user32.PostThreadMessageW(self._thread_id, WM_APP_DISARM, 0, 0)

    def shutdown(self) -> None:
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(1)

    # ------------------------------------------------------------ поток хуков
    def _run(self) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_ARM:
                self._install()
            elif msg.message == WM_APP_DISARM:
                self._uninstall()
        self._uninstall()

    def _install(self) -> None:
        self._fired = False
        if self._kb:
            return
        hmod = kernel32.GetModuleHandleW(None)
        self._kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._ms = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, hmod, 0)
        if not self._kb or not self._ms:
            log.warning("Не удалось поставить хук ввода: ошибка %s", ctypes.get_last_error())
        self._armed = True

    def _uninstall(self) -> None:
        self._armed = False
        for h in (self._kb, self._ms):
            if h:
                user32.UnhookWindowsHookEx(h)
        self._kb = self._ms = None

    def _current_mods(self) -> int:
        mods = 0
        for flag, vk in _MOD_VK:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                mods |= flag
        return mods

    # Колбэки должны отрабатывать быстро (иначе Windows снимет хук), поэтому только флаги и emit.
    def _on_key(self, code, wparam, lparam):
        if code == 0 and self._armed and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not k.flags & LLKHF_INJECTED and k.vkCode not in _MODIFIERS \
                    and (self._current_mods(), k.vkCode) not in self._hotkeys:
                if not self._fired:
                    self._fired = True
                    self.tripped.emit("key")
                return 1  # поглотить: случайная клавиша не должна попасть в код
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_mouse(self, code, wparam, lparam):
        if code == 0 and self._armed and wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN,
                                                   WM_XBUTTONDOWN):
            m = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not m.flags & LLMHF_INJECTED and not self._fired:
                self._fired = True
                self.tripped.emit("mouse")
            # клик не поглощаем: пользователь, возможно, хочет переключить окно
        return user32.CallNextHookEx(None, code, wparam, lparam)
