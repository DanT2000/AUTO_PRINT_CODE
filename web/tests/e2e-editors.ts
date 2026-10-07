// Сквозной тест печати Node-движком в настоящие окна (как tests/run_editors.py у Python-версии).
//
//   node tests/e2e-editors.ts tk notepad
//
// tk      — тестовое окно tests/tk_target.py: plain и ide (само дописывает скобки и автоотступ, как редактор)
// notepad — Блокнот Windows: печать, Ctrl+S, сравнение файла с образцом (оба профиля)
// Во время прогона НЕ трогать клавиатуру и мышь — тест управляет фокусом.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import koffi from "koffi";
import { DEFAULT_SETTINGS, type Settings } from "../shared/model.ts";
import { Engine } from "../server/engine.ts";
import * as w from "../server/win32.ts";

const ROOT = path.resolve(import.meta.dirname, "..", "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "apc-e2e-"));
const user32 = koffi.load("user32.dll");
const EnumProc = koffi.proto("bool __stdcall EnumProc(intptr_t hwnd, intptr_t lParam)");
const EnumWindows = user32.func("bool __stdcall EnumWindows(EnumProc *cb, intptr_t lParam)");
const IsWindowVisible = user32.func("bool __stdcall IsWindowVisible(intptr_t hwnd)");
const SetForegroundWindow = user32.func("bool __stdcall SetForegroundWindow(intptr_t hwnd)");

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function activate(part: string): void {
  const found: number[] = [];
  const cb = koffi.register((h: number) => {
    if (IsWindowVisible(h) && w.windowTitle(h).toLowerCase().includes(part.toLowerCase())) found.push(Number(h));
    return true;
  }, koffi.pointer(EnumProc));
  EnumWindows(cb, 0);
  koffi.unregister(cb);
  if (found.length) {
    w.tap(w.VK_MENU);   // нажатие Alt снимает запрет Windows на смену фокуса
    SetForegroundWindow(found[0]);
  }
}

async function waitFg(part: string, timeoutMs = 20000): Promise<boolean> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (w.windowTitle(w.foregroundWindow()).toLowerCase().includes(part.toLowerCase())) return true;
    activate(part);
    await sleep(400);
  }
  console.log("  в фокусе:", w.describeWindow(w.foregroundWindow()));
  return false;
}

async function typeText(code: string, profile: string, requireTitle: string): Promise<{ state: string; msgs: string[] }> {
  const s: Settings = { ...DEFAULT_SETTINGS, profile, cpm: 1500, jitter: 0, newline_pause_ms: 40, punct_pause_ms: 0,
    indent_with_tab: !!process.env.AP_TAB };
  const e = new Engine(s, "⌨ AutoPrintCode", { requireTitle });   // в чужое окно — ни одного нажатия
  const msgs: string[] = [];
  const done = new Promise<void>((resolve) => e.on("event", (ev) => {
    if (process.env.DEBUG && ev.e !== "progress" && ev.e !== "sound") console.log("  ", JSON.stringify(ev));
    if (ev.e === "message") msgs.push(ev.text);
    // сообщение об ошибке приходит сразу после смены состояния — даём ему дойти
    if (ev.e === "state" && (ev.state === "finished" || ev.state === "paused")) setTimeout(resolve, 200);
  }));
  e.load(code);
  e.start(0.05, 0);
  await done;
  const state = e.state;
  await e.close();
  return { state, msgs };
}

const PY = `import os


def load(path, default=None):
    """Читает файл."""
    if not os.path.exists(path):
        return default or {}
    with open(path, encoding="utf-8") as f:
        data = [line.strip() for line in f if line]
    items = {"a": [1, 2], "b": (3, 4)}
    print(f"Строк: {len(data)}", items['a'])
    return data


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y`;

let failures = 0;
function report(name: string, got: string, exp: string, extra: string): void {
  const ok = got.replace(/\n+$/, "") === exp.replace(/\n+$/, "");
  if (!ok) failures++;
  console.log(`[${name}] ${ok ? "MATCH" : "DIFF"} ${extra}`);
  if (!ok) console.log("  GOT:", JSON.stringify(got), "\n  EXP:", JSON.stringify(exp));
}

async function runTk(mode: "plain" | "ide"): Promise<void> {
  const out = path.join(TMP, `tk_${mode}.txt`);
  spawn("python", [path.join(ROOT, "tests", "tk_target.py"), mode, out], { stdio: "ignore" });
  if (!(await waitFg("TARGET"))) return void console.log(`[tk/${mode}] окно не получило фокус`);
  await sleep(500);
  const r = await typeText(PY, mode, "TARGET");
  for (let i = 0; i < 100 && !fs.existsSync(out); i++) await sleep(100);   // окно само сохранит текст и закроется
  report(`tk/${mode}`, fs.readFileSync(out, "utf-8").replace(/\r\n/g, "\n"), PY, `state=${r.state} ${JSON.stringify(r.msgs)}`);
}

async function runNotepad(profile: "plain" | "ide"): Promise<void> {
  const file = path.join(TMP, `np_${profile}.txt`);
  fs.writeFileSync(file, "");
  spawn("notepad.exe", [file], { stdio: "ignore", detached: true }).unref();
  if (!(await waitFg(path.basename(file), 30000))) return void console.log(`[notepad/${profile}] окно не получило фокус`);
  await sleep(1500);
  const r = await typeText(PY, profile, path.basename(file));
  await sleep(500);
  w.tap("S".charCodeAt(0), w.VK_CONTROL);
  await sleep(1000);
  report(`notepad/${profile}`, fs.readFileSync(file, "utf-8").replace(/^﻿/, "").replace(/\r\n/g, "\n"), PY,
    `state=${r.state} ${JSON.stringify(r.msgs)}`);
  w.tap("W".charCodeAt(0), w.VK_CONTROL);
  await sleep(800);
}

const which = process.argv.slice(2);
if (which.includes("tk")) { await runTk("plain"); await runTk("ide"); }
if (which.includes("notepad")) { await runNotepad("plain"); await runNotepad("ide"); }
process.exit(failures ? 1 : 0);
