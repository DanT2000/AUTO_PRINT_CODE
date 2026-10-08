"""Защита набора от случайного вмешательства пользователя.

Пока идёт печать, ставится низкоуровневый хук клавиатуры (WH_KEYBOARD_LL). Физическое
нажатие клавиши → сигнал tripped, движок встаёт на паузу. Нажатая клавиша поглощается,
чтобы не попасть в код. Свои синтетические события (флаг INJECTED) пропускаются. Хук
снимается сразу после паузы/остановки — вне печати приложение клавиатуру не слушает.
Ничего не записывается: проверяется только факт нажатия.

Клики мыши хуком не ловятся: хук мыши на Python пропускал бы через себя каждое движение
курсора во всей системе (подтормаживание). Вместо него на время печати включается Raw Input
(WM_INPUT): Windows лишь присылает копию событий мыши, ничего не ждёт от программы — курсор не
тормозит. Каждое нажатие кнопки мыши не над окном самой программы увеличивает счётчик click_count;
движок сверяет его перед каждым символом (typer._mouse_interrupt) — так ловится и короткий тап
тачпада, который опрос «кнопка нажата сейчас» пропустил бы. Клики по окну программы — не вмешательство.

Режим «ждём Enter» (печать по строкам): ставится только хук клавиатуры, физический Enter без
модификаторов сообщается сигналом enter_pressed и НЕ поглощается — он доходит до редактора и
создаёт новую строку, после чего программа печатает следующую.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from . import winapi

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_INJECTED = 0x10
WM_QUIT = 0x0012
WM_APP_ARM = 0x8000 + 10
WM_APP_DISARM = 0x8000 + 11
WM_APP_WATCH = 0x8000 + 12
WM_APP_UNWATCH = 0x8000 + 13
VK_RETURN = 0x0D

# Модификаторы сами по себе паузу не вызывают: ими начинается хоткей (Ctrl+F9 и т.п.).
_MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x14, 0x90, 0x91}
_MOD_VK = ((0x0002, 0x11), (0x0001, 0x12), (0x0004, 0x10), (0x0008, 0x5B), (0x0008, 0x5C))

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
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

# ---- Raw Input: нажатия кнопок мыши во время печати
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_REMOVE, RIDEV_INPUTSINK = 0x00000001, 0x00000100
HWND_MESSAGE = -3
# RI_MOUSE_LEFT/RIGHT/MIDDLE/BUTTON_4/BUTTON_5 _DOWN
_BUTTONS_DOWN = 0x0001 | 0x0004 | 0x0010 | 0x0040 | 0x0100


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE),
                ("wParam", wintypes.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    # после usFlags — объединение с ULONG ulButtons, оно выровнено на 4: usButtonFlags лежит по смещению 4
    _fields_ = [("usFlags", wintypes.USHORT), ("_pad", wintypes.USHORT), ("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT), ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUTMOUSE(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                   wintypes.UINT)
user32.GetRawInputData.restype = wintypes.UINT
user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)


class InputGuard(QObject):
    tripped = Signal(str)   # "key"
    enter_pressed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.enabled = True
        self._hotkeys: set[tuple[int, int]] = set()
        self._armed = False
        self._watching = False
        self._fired = False
        self._kb = None
        self._raw_hwnd = None      # окно-получатель Raw Input (только сообщения, на экране его нет)
        self._raw_on = False
        self._clicks = 0           # нажатия кнопок мыши не над окном программы, пока идёт печать
        self._thread_id = 0
        self._ready = threading.Event()
        # ссылку на колбэк держим, иначе сборщик мусора его удалит → падение процесса
        self._kb_proc = HOOKPROC(self._on_key)
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

    def watch_enter(self, on: bool) -> None:
        """Печать по строкам: сообщать о физическом Enter (не поглощая его)."""
        user32.PostThreadMessageW(self._thread_id, WM_APP_WATCH if on else WM_APP_UNWATCH, 0, 0)

    def shutdown(self) -> None:
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(1)

    def click_count(self) -> int:
        """Сколько раз за время печати нажимали кнопку мыши не над окном программы (для движка)."""
        return self._clicks

    # ------------------------------------------------------------ поток хуков
    def _run(self) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = kernel32.GetCurrentThreadId()
        # окно только для сообщений (HWND_MESSAGE): в него Windows присылает WM_INPUT
        self._raw_hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None,
                                                kernel32.GetModuleHandleW(None), None)
        if not self._raw_hwnd:
            log.warning("Raw Input недоступен (ошибка %s) — клик мышью ловится только опросом",
                        ctypes.get_last_error())
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_INPUT:
                self._on_raw(msg.lParam)
                user32.DispatchMessageW(ctypes.byref(msg))   # DefWindowProc освобождает данные WM_INPUT
                continue
            if msg.message == WM_APP_ARM:
                self._armed = True
                self._fired = False
            elif msg.message == WM_APP_DISARM:
                self._armed = False
            elif msg.message == WM_APP_WATCH:
                self._watching = True
            elif msg.message == WM_APP_UNWATCH:
                self._watching = False
            else:
                continue
            self._sync_hooks()
        self._armed = self._watching = False
        self._sync_hooks()
        if self._raw_hwnd:
            user32.DestroyWindow(self._raw_hwnd)

    def _sync_raw(self, on: bool) -> None:
        """Raw Input мыши — только пока печатаем (в ожидании Enter и на паузе клики не нужны)."""
        if on == self._raw_on or not self._raw_hwnd:
            return
        dev = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK if on else RIDEV_REMOVE,
                             self._raw_hwnd if on else None)   # Generic Desktop / Mouse
        if user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(RAWINPUTDEVICE)):
            self._raw_on = on
        elif on:
            log.warning("Raw Input мыши не включён: ошибка %s — клик ловится только опросом",
                        ctypes.get_last_error())

    def _on_raw(self, handle) -> None:
        if not self._raw_on:
            return
        data = RAWINPUTMOUSE()
        size = wintypes.UINT(ctypes.sizeof(data))
        got = user32.GetRawInputData(handle, RID_INPUT, ctypes.byref(data), ctypes.byref(size),
                                     ctypes.sizeof(RAWINPUTHEADER))
        if got == 0xFFFFFFFF or got < ctypes.sizeof(RAWINPUTHEADER) or data.header.dwType != RIM_TYPEMOUSE:
            return
        if data.mouse.usButtonFlags & _BUTTONS_DOWN and not winapi.cursor_over_own_window():
            self._clicks += 1

    def _sync_hooks(self) -> None:
        """Хук клавиатуры — пока печатаем или ждём Enter. Хука мыши нет (хук на Python тормозил бы курсор
        во всей системе): пока печатаем, нажатия кнопок мыши приходят через Raw Input."""
        self._sync_raw(self._armed)
        hmod = kernel32.GetModuleHandleW(None)
        need_kb = self._armed or self._watching
        if need_kb and not self._kb:
            self._kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
            if not self._kb:
                log.warning("Не удалось поставить хук клавиатуры: ошибка %s", ctypes.get_last_error())
        elif not need_kb and self._kb:
            user32.UnhookWindowsHookEx(self._kb)
            self._kb = None

    def _current_mods(self) -> int:
        mods = 0
        for flag, vk in _MOD_VK:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                mods |= flag
        return mods

    # Колбэки должны отрабатывать быстро (иначе Windows снимет хук), поэтому только флаги и emit.
    def _on_key(self, code, wparam, lparam):
        if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) and (self._armed or self._watching):
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            injected = bool(k.flags & LLKHF_INJECTED)
            if self._armed:
                if not injected and k.vkCode not in _MODIFIERS \
                        and (self._current_mods(), k.vkCode) not in self._hotkeys:
                    if not self._fired:
                        self._fired = True
                        self.tripped.emit("key")
                    return 1  # поглотить: случайная клавиша не должна попасть в код
            elif not injected and k.vkCode == VK_RETURN and not self._current_mods():
                self.enter_pressed.emit()   # не поглощаем: Enter должен дойти до редактора
        return user32.CallNextHookEx(None, code, wparam, lparam)
