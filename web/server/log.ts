// Журнал: data/logs/autoprint-web.log (ротация 3 × 1 МБ).
// Пишутся только служебные события: старт/пауза/стоп, причина, целевое окно, ошибки.
// Набираемый текст и нажатия пользователя в журнал НЕ попадают.
import fs from "node:fs";
import path from "node:path";
import { DATA_DIR } from "./store.ts";

export const LOG_FILE = path.join(DATA_DIR, "logs", "autoprint-web.log");
const MAX = 1_000_000;

function rotate(): void {
  try {
    if (fs.statSync(LOG_FILE).size < MAX) return;
  } catch {
    return;
  }
  for (let i = 2; i >= 1; i--) {
    if (fs.existsSync(`${LOG_FILE}.${i}`)) fs.renameSync(`${LOG_FILE}.${i}`, `${LOG_FILE}.${i + 1}`);
  }
  fs.renameSync(LOG_FILE, `${LOG_FILE}.1`);
}

export function log(level: "info" | "warn" | "error", text: string): void {
  const d = new Date();
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().replace("T", " ").slice(0, 23);
  const line = `${local} ${level.toUpperCase().padEnd(5)} ${text}`;
  (level === "info" ? console.log : console.error)(line);
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    rotate();
    fs.appendFileSync(LOG_FILE, line + "\n", "utf-8");
  } catch {
    // журнал не должен ронять помощника
  }
}
