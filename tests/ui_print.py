"""Сквозная проверка нового интерфейса: главное окно готовит текст активного блока и печатает его в Блокнот.

    python tests/ui_print.py [block steps lines prompter vscode]

Печать запускается так же, как по хоткею (cmd_toggle). Проверяются оба профиля и режим «Без комментариев»
(в нём ход печати сопоставляется с исходным блоком по карте позиций). Во время прогона НЕ трогать
клавиатуру и мышь. Рабочая папка data/ не используется.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SP = tempfile.mkdtemp(prefix="autoprint-uiprint-")
os.environ["AUTOPRINT_DATA"] = os.path.join(SP, "data")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from autoprint import winapi as w  # noqa: E402
from autoprint.comments import strip_comments  # noqa: E402
from autoprint.storage import Settings, TemplateStore  # noqa: E402
from autoprint.typer import FINISHED  # noqa: E402
from autoprint.ui.main_window import MainWindow  # noqa: E402
from autoprint.ui.theme import theme  # noqa: E402

_u = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def activate(part: str) -> None:
    found = []

    def cb(h, _):
        if _u.IsWindowVisible(h) and part.lower() in w.window_title(h).lower():
            found.append(h)
        return True
    _u.EnumWindows(_EnumProc(cb), 0)
    if found:
        w.tap(w.VK_MENU)
        _u.SetForegroundWindow(found[0])


def wait_fg(part: str, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if part.lower() in w.window_title(w.foreground_window()).lower():
            return True
        activate(part)
        time.sleep(0.5)
    return False


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def run(win: MainWindow, name: str, profile: str, strip: bool) -> bool:
    s = win.settings
    s.profile, s.strip_comments = profile, strip
    win._apply_settings()
    win._invalidate_job_if_idle()
    v = win.current_view()
    block = v.template.find_block(v.template.active_block)
    expected = strip_comments(block.text, block.lang)[0] if strip else block.text
    path = os.path.join(SP, f"{name}.txt")
    open(path, "w", encoding="utf-8").write("")
    subprocess.Popen(["notepad.exe", path])
    if not wait_fg(f"{name}.txt"):
        print(f"[{name}] Блокнот не получил фокус")
        return False
    pump(1.5)
    win.cmd_toggle()   # как по хоткею
    seen_progress = set()
    end = time.time() + 90
    while time.time() < end and win.engine.state != FINISHED:
        app.processEvents()
        cw = v.code_widget(block.id)
        if cw.editor._progress:
            seen_progress.add(cw.editor._progress[1])
        time.sleep(0.01)
    pump(0.5)
    w.tap(ord("S"), w.VK_CONTROL)
    time.sleep(1.0)
    got = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    ok = got.rstrip("\n") == expected.rstrip("\n")
    lines = win.strip.count.text()
    print(f"[{name}] state={win.engine.state} {'MATCH' if ok else 'DIFF'} · пульт: «{lines}» · "
          f"позиций подсветки: {len(seen_progress)}")
    if not ok:
        print("  GOT:", repr(got))
        print("  EXP:", repr(expected))
    w.tap(ord("W"), w.VK_CONTROL)
    pump(1.0)
    total = expected.count("\n") + 1
    return ok and len(seen_progress) > 5 and lines == f"{total} / {total} стр."


def open_editor(cmd: list[str], path: str, settle: float) -> bool:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("VSCODE", "ELECTRON"))}
    subprocess.Popen(cmd + [path], env=env)
    if not wait_fg(os.path.basename(path), 60):
        print(f"[{os.path.basename(path)}] редактор не получил фокус")
        return False
    pump(settle)
    return True


def wait_state(win: MainWindow, states: tuple, timeout: float = 120) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if win.engine.state in states:
            return True
        time.sleep(0.01)
    return False


def run_steps(win: MainWindow, name: str, profile: str, cmd: list[str], settle: float = 1.5) -> bool:
    """Пошаговый урок: шаг за шагом, с возвратом наверх; итог = код без комментариев."""
    from autoprint.typer import IDLE
    s = win.settings
    s.profile, s.strip_comments = profile, True
    t = next(t for t in win.store.templates if t.title.startswith("Пример: пошаговый"))
    win.open_template(t.id)
    block = t.code_blocks()[0]
    win.current_view().arm(block.id)
    win._on_mode("steps")
    win._apply_settings()
    win.step_next.pop(block.id, None)
    expected = strip_comments(block.text, block.lang)[0]
    path = os.path.join(SP, f"{name}.{'py' if 'vs' in name else 'txt'}")
    open(path, "w", encoding="utf-8").write("")
    if not open_editor(cmd, path, settle):
        return False
    n_steps = len(win._block_steps(block))
    for i in range(n_steps):
        win.cmd_toggle()
        if not wait_state(win, (FINISHED,)):
            print(f"[{name}] шаг {i + 1} не закончился, состояние {win.engine.state}")
            return False
        pump(0.4)
    pump(0.5)
    w.tap(ord("S"), w.VK_CONTROL)
    time.sleep(1.2)
    got = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    ok = got.rstrip("\n") == expected.rstrip("\n")
    done = win._step_pointer(block.id) is None
    print(f"[{name}] шагов {n_steps} · {'MATCH' if ok else 'DIFF'} · указатель в конце: "
          f"{'все шаги' if done else win._step_pointer(block.id)}")
    if not ok:
        print("  GOT:", repr(got))
        print("  EXP:", repr(expected))
    w.tap(ord("W"), w.VK_CONTROL)
    pump(1.0)
    win._on_mode("block")
    return ok and done and win.engine.state in (FINISHED, IDLE)


def click(widget) -> None:
    """Настоящий щелчок мышью по середине виджета (курсор ставит Qt — с учётом масштаба экрана)."""
    from PySide6.QtGui import QCursor
    QCursor.setPos(widget.mapToGlobal(widget.rect().center()))
    time.sleep(0.15)
    _u.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.05)
    _u.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP


def run_prompter(win: MainWindow, name: str) -> bool:
    """Окно суфлёра вживую: щелчок по «Дальше» печатает шаг в Блокнот — фокус остаётся в Блокноте
    (окно суфлёра не активируется); «Печатаю сам» — щелчки листают шаги и ничего не печатают."""
    s = win.settings
    s.profile, s.strip_comments = "plain", True
    t = next(t for t in win.store.templates if t.title.startswith("Пример: пошаговый"))
    win.open_template(t.id)
    block = t.code_blocks()[0]
    win.current_view().arm(block.id)
    win._on_mode("steps")
    win._apply_settings()
    win.step_next.pop(block.id, None)
    ctl = win.prompter_ctl
    ctl.show()
    pump(0.5)
    pw = ctl.window
    from autoprint.ui.frameless import user32
    ex = user32.GetWindowLongPtrW(int(pw.winId()), -20)
    noact = bool(ex & 0x08000000)
    path = os.path.join(SP, f"{name}.txt")
    open(path, "w", encoding="utf-8").write("")
    if not open_editor(["notepad.exe"], path, 1.5):
        return False
    n_steps = len(win._block_steps(block))
    focus_kept = True
    for i in range(n_steps):
        click(pw.btn_next)
        from autoprint.typer import COUNTDOWN, RUNNING
        started = wait_state(win, (RUNNING, COUNTDOWN), 10)   # сначала — началась печать этого шага
        if not started or not wait_state(win, (FINISHED,)):
            print(f"[{name}] шаг {i + 1} не напечатан, состояние {win.engine.state}")
            return False
        pump(0.4)
        focus_kept = focus_kept and f"{name}.txt" in w.window_title(w.foreground_window())
    expected = strip_comments(block.text, block.lang)[0]
    w.tap(ord("S"), w.VK_CONTROL)
    time.sleep(1.2)
    got = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    ok = got.rstrip("\n") == expected.rstrip("\n")
    # «Печатаю сам»: щелчки листают шаги, в Блокнот ничего не попадает
    click(pw.mode.buttons["self"])
    pump(0.3)
    before = ctl.card().title
    click(pw.btn_next)
    pump(0.5)
    after = ctl.card().title
    w.tap(ord("S"), w.VK_CONTROL)
    time.sleep(0.8)
    same = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n") == got
    print(f"[{name}] окно не активируется: {noact} · шагов {n_steps} щелчками · {'MATCH' if ok else 'DIFF'} · "
          f"фокус остался в Блокноте: {focus_kept} · «печатаю сам»: «{before}» → «{after}», "
          f"в Блокноте без изменений: {same}")
    if not ok:
        print("  GOT:", repr(got))
        print("  EXP:", repr(expected))
    w.tap(ord("W"), w.VK_CONTROL)
    pump(1.0)
    pw.mode_changed.emit("auto")
    pw.close()
    win._on_mode("block")
    return ok and noact and focus_kept and before != after and same


def run_lines(win: MainWindow, name: str, profile: str) -> bool:
    """По строкам: после каждой строки ждём; «Enter пользователя» и хоткей — по очереди."""
    from autoprint.typer import LINE_WAIT
    s = win.settings
    s.profile, s.strip_comments = profile, False
    win.open_template(win.store.templates[0].id)
    v = win.current_view()
    block = v.template.code_blocks()[0]
    v.arm(block.id)
    win._on_mode("lines")
    win._apply_settings()
    expected = block.text
    path = os.path.join(SP, f"{name}.txt")
    open(path, "w", encoding="utf-8").write("")
    if not open_editor(["notepad.exe"], path, 1.5):
        return False
    win.cmd_toggle()
    waits = 0
    end = time.time() + 120
    while time.time() < end and win.engine.state != FINISHED:
        app.processEvents()
        if win.engine.state == LINE_WAIT:
            waits += 1
            pump(0.15)
            if waits % 2:
                w.tap(w.VK_RETURN)                 # как будто Enter нажал человек
                win.engine.continue_line(by_enter=True)
            else:
                win.cmd_toggle()                   # хоткей «дальше»
            wait_state(win, (FINISHED, "running"), 10)
        time.sleep(0.01)
    pump(0.5)
    w.tap(ord("S"), w.VK_CONTROL)
    time.sleep(1.0)
    got = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    ok = got.rstrip("\n") == expected.rstrip("\n")
    nonblank = sum(1 for ln in expected.split("\n")[:-1] if ln.strip())
    print(f"[{name}] ожиданий Enter: {waits} (ожидалось {nonblank}) · {'MATCH' if ok else 'DIFF'}")
    if not ok:
        print("  GOT:", repr(got))
        print("  EXP:", repr(expected))
    w.tap(ord("W"), w.VK_CONTROL)
    pump(1.0)
    win._on_mode("block")
    return ok and waits == nonblank


# AP_VSCODE — другой Code.exe (например, портативный), см. run_editors.py
VSCODE = Path(os.environ.get("AP_VSCODE") or
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe")


def main() -> int:
    s = Settings()
    s.cpm, s.jitter, s.newline_pause_ms, s.punct_pause_ms = 1500, 0, 40, 0
    s.sound_enabled = False
    theme.setup(app, "dark")
    win = MainWindow(s, TemplateStore())
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    pump(0.5)
    which = set(sys.argv[1:]) or {"block", "steps", "lines"}
    results = []
    if "block" in which:
        results += [run(win, "ui_plain", "plain", False), run(win, "ui_ide_nocomments", "ide", True)]
    if "steps" in which:
        results += [run_steps(win, "ui_steps_plain", "plain", ["notepad.exe"]),
                    run_steps(win, "ui_steps_ide", "ide", ["notepad.exe"])]
    if "lines" in which:
        results += [run_lines(win, "ui_lines_plain", "plain"), run_lines(win, "ui_lines_ide", "ide")]
    if "prompter" in which:
        results.append(run_prompter(win, "ui_prompter"))
    if "vscode" in which:
        if VSCODE.exists():
            cmd = [str(VSCODE), f"--user-data-dir={SP}/vscode-data", f"--extensions-dir={SP}/vscode-ext",
                   "--disable-extensions", "--skip-welcome", "--skip-release-notes", "--disable-workspace-trust",
                   "-n"]
            results.append(run_steps(win, "ui_steps_vscode", "ide", cmd, settle=8))
        else:
            print("[vscode] VS Code не найден — пропуск")
    win.close()
    print("OK" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
