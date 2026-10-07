// Хранилище: settings.json и templates.json в web/data/ — тот же формат, что у Python-версии.
// При первом запуске копируются образцы и настройки Python-версии (../data), если они есть.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  mergeSettings, sampleTemplate, type Settings, type Template, templateFrom,
} from "../shared/model.ts";

export const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const DATA_DIR = path.resolve(process.env.AUTOPRINT_DATA || path.join(WEB_DIR, "data"));
const PY_DATA_DIR = path.resolve(WEB_DIR, "..", "data");
const SETTINGS_FILE = path.join(DATA_DIR, "settings.json");
const TEMPLATES_FILE = path.join(DATA_DIR, "templates.json");

function atomicWrite(file: string, data: unknown, backup = false): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = file + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf-8");
  if (backup && fs.existsSync(file)) fs.renameSync(file, file.replace(/\.json$/, ".json.bak"));
  fs.renameSync(tmp, file);
}

function readJson(file: string): Record<string, unknown> | null {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw new Error(`Не удалось прочитать ${file}: ${(e as Error).message}`);
  }
}

/** Первый запуск: забрать данные Python-версии (копия — исходные файлы не меняются). */
function adoptPythonData(): string {
  if (process.env.AUTOPRINT_DATA || fs.existsSync(TEMPLATES_FILE)) return "";
  const src = path.join(PY_DATA_DIR, "templates.json");
  if (!fs.existsSync(src)) return "";
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.copyFileSync(src, TEMPLATES_FILE);
  const st = path.join(PY_DATA_DIR, "settings.json");
  if (fs.existsSync(st) && !fs.existsSync(SETTINGS_FILE)) fs.copyFileSync(st, SETTINGS_FILE);
  return src;
}

export class Store {
  settings: Settings;
  templates: Template[];
  readonly adoptedFrom: string;
  private saveTimer: NodeJS.Timeout | null = null;

  constructor() {
    this.adoptedFrom = adoptPythonData();
    this.settings = mergeSettings(readJson(SETTINGS_FILE) ?? {});
    const raw = readJson(TEMPLATES_FILE);
    if (raw === null) {
      this.templates = [sampleTemplate()];
      this.saveTemplatesNow();
    } else {
      this.templates = ((raw.templates as Record<string, unknown>[]) ?? []).map(templateFrom);
    }
  }

  get(id: string): Template | undefined {
    return this.templates.find((t) => t.id === id);
  }

  put(t: Template): void {
    const i = this.templates.findIndex((x) => x.id === t.id);
    if (i >= 0) this.templates[i] = t;
    else this.templates.push(t);
    this.saveTemplates();
  }

  remove(id: string): void {
    this.templates = this.templates.filter((t) => t.id !== id);
    this.saveTemplates();
  }

  saveSettings(): void {
    atomicWrite(SETTINGS_FILE, this.settings);
  }

  /** Сохранение с задержкой: правки в редакторе идут потоком. */
  saveTemplates(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveTemplatesNow(), 700);
  }

  saveTemplatesNow(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = null;
    atomicWrite(TEMPLATES_FILE, { version: 3, templates: this.templates }, true);
  }
}
