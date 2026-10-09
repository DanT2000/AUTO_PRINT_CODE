"""Подключение нейросети — так же, как в PasteTalk.

Провайдеры двух видов:
- «openai» — любой сервер с /v1/chat/completions: ChatGPT (OpenAI), DeepSeek, AITunnel, LM Studio и Ollama
  на этом компьютере, своё OpenAI-совместимое;
- «cli» — агент, уже установленный на компьютере (Claude Code, Codex): работает по подписке человека,
  ключ не нужен. Запрос уходит в stdin.

Один запасной провайдер: основной не ответил (не отмена) — тот же запрос один раз уходит запасному.
Ключи хранятся в settings.json зашифрованными Windows (DPAPI, «dpapi:…»): прочитать их может только эта
учётная запись на этом компьютере. На другом компьютере ключ придётся ввести заново.

Модуль без Qt: функции блокирующие, вызываются из фонового потока (ui/import_dialog.py).
"""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger("autoprint.ai")


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str                    # openai | cli
    url: str = ""                # адрес по умолчанию (openai)
    model: str = ""              # модель по умолчанию
    needs_key: bool = False
    json_mode: bool = False      # сервер понимает response_format: json_object
    models: tuple = ()           # фиксированный список моделей (cli)
    hint: str = ""


PROVIDERS: tuple[Provider, ...] = (
    Provider("claudeCli", "Claude — по подписке (Claude Code)", "cli", model="sonnet",
             models=("haiku", "sonnet", "opus"),
             hint="Через установленную программу Claude Code и вашу подписку Claude — ключ не нужен. "
                  "sonnet — лучшее соотношение качества и скорости, haiku — быстрее, opus — сильнее."),
    Provider("codexCli", "ChatGPT — по подписке (Codex CLI)", "cli", model="",
             hint="Через установленную программу Codex и вашу подписку ChatGPT — ключ не нужен."),
    Provider("openai", "ChatGPT (OpenAI API)", "openai", url="https://api.openai.com/v1", model="gpt-4o-mini",
             needs_key=True, json_mode=True, hint="Ключ — на platform.openai.com → API keys. Оплата по запросам."),
    Provider("deepseek", "DeepSeek", "openai", url="https://api.deepseek.com/v1", model="deepseek-chat",
             needs_key=True, json_mode=True, hint="Ключ — на platform.deepseek.com. Недорого и хорошо с кодом."),
    Provider("aitunnel", "AITunnel (доступ из России)", "openai", url="https://api.aitunnel.ru/v1",
             model="deepseek-chat", needs_key=True,
             hint="Шлюз к разным моделям с оплатой из России. Ключ — на aitunnel.ru."),
    Provider("lmstudio", "LM Studio (на этом компьютере)", "openai", url="http://localhost:1234/v1",
             hint="Модель запущена в LM Studio на этом компьютере — код никуда не уходит. Для разбора кода "
                  "нужна модель от 7B, лучше специальная для кода (Qwen Coder и т. п.)."),
    Provider("ollama", "Ollama (на этом компьютере)", "openai", url="http://localhost:11434/v1",
             model="qwen2.5-coder:7b",
             hint="Модель запущена в Ollama на этом компьютере — код никуда не уходит."),
    Provider("custom", "Своё, OpenAI-совместимое", "openai",
             hint="Любой сервер с /v1/chat/completions: OpenRouter, Groq, свой и т. п."),
)
_BY_ID = {p.id: p for p in PROVIDERS}


def provider(pid: str) -> Provider | None:
    return _BY_ID.get(pid)


class AIError(Exception):
    """Понятная человеку ошибка запроса."""


class Cancelled(AIError):
    pass


# ---------------------------------------------------------------- ключи: DPAPI

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):   # UI_FORBIDDEN
        raise OSError(ctypes.GetLastError(), "DPAPI")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)


def protect(secret: str) -> str:
    if not secret:
        return ""
    try:
        return "dpapi:" + base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii")
    except (OSError, AttributeError):   # не Windows — хранить как есть лучше, чем потерять
        return "plain:" + secret


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("plain:"):
        return stored[6:]
    if stored.startswith("dpapi:"):
        try:
            return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")
        except (OSError, ValueError, AttributeError):
            return ""   # другой компьютер или учётная запись — ключ придётся ввести заново
    return stored


# ---------------------------------------------------------------- настройки провайдера

@dataclass
class Config:
    provider: Provider
    url: str
    key: str
    model: str


def config(settings, pid: str | None = None) -> Config | None:
    """Адрес, ключ и модель провайдера pid (по умолчанию — основного) из настроек."""
    p = provider(pid if pid is not None else settings.ai_provider)
    if p is None:
        return None
    return Config(p, (settings.ai_urls.get(p.id) or p.url).strip(), unprotect(settings.ai_keys.get(p.id, "")),
                  (settings.ai_models.get(p.id) or p.model).strip())


def configured(settings) -> bool:
    """Основной провайдер выбран и для него есть всё нужное."""
    c = config(settings)
    if c is None:
        return False
    if c.provider.kind == "cli":
        return cli_path(c.provider) is not None
    return bool(c.url) and (bool(c.key) or not c.provider.needs_key)


def problem(c: Config) -> str | None:
    """Почему с этими настройками запрос не уйдёт (None — уйдёт)."""
    if c.provider.kind == "cli":
        if cli_path(c.provider) is None:
            prog = "Claude Code" if c.provider.id == "claudeCli" else "Codex"
            return f"Не найдена программа {prog} — установите её или выберите другого провайдера."
        return None
    if not c.url:
        return "Не указан адрес сервера."
    if c.provider.needs_key and not c.key:
        return "Не указан ключ доступа."
    return None


# ---------------------------------------------------------------- запросы

def timeout_for(settings, text_len: int, cli: bool) -> float:
    if settings.ai_timeout_s > 0:
        return float(settings.ai_timeout_s)
    t = min(60 + text_len * 0.03, 300)
    return max(t, 180) if cli else t


def chat_url(base: str) -> str:
    b = base.rstrip("/")
    if not re.search(r"/v\d+$", b):
        b += "/v1"
    return b + "/chat/completions"


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _clean(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _http_json(url: str, payload: dict | None, key: str, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": "AutoPrintCode"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:400]
        msg = body
        try:
            j = json.loads(body)
            msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("error") or body
        except ValueError:
            pass
        err = AIError(_http_hint(e.code, str(msg)))
        err.status = e.code
        raise err from None
    except urllib.error.URLError as e:
        raise AIError(f"Сервер не отвечает: {e.reason}. Проверьте адрес и что сервер запущен.") from None
    except TimeoutError:
        raise AIError("Нейросеть не ответила вовремя — попробуйте ещё раз или модель полегче.") from None
    except ValueError:
        raise AIError("Сервер ответил не JSON — проверьте адрес (нужен OpenAI-совместимый /v1).") from None


def _http_hint(code: int, msg: str) -> str:
    if code == 401:
        return "Ключ не подошёл (401) — проверьте ключ доступа."
    if code == 402:
        return "На счёте закончились деньги (402)."
    if code == 404:
        return f"Не найдено (404): {msg[:160]} — проверьте адрес и название модели."
    if code == 429:
        return "Слишком много запросов или исчерпан лимит (429) — подождите и попробуйте снова."
    return f"Ошибка сервера {code}: {msg[:200]}"


def _openai_chat(c: Config, system: str, user: str, timeout: float, json_mode: bool) -> str:
    payload = {"model": c.model, "temperature": 0.2, "stream": False,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode and c.provider.json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        data = _http_json(chat_url(c.url), payload, c.key, timeout)
    except AIError as e:
        if getattr(e, "status", 0) in (400, 422) and "response_format" in payload:
            payload.pop("response_format")   # сервер не понял «ответ строго JSON» — без него
            data = _http_json(chat_url(c.url), payload, c.key, timeout)
        else:
            raise
    try:
        return _clean(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        raise AIError("Сервер ответил не в формате chat/completions.") from None


def cli_path(p: Provider) -> str | None:
    return shutil.which("claude" if p.id == "claudeCli" else "codex")


def _cli_args(c: Config) -> list[str]:
    exe = cli_path(c.provider)
    if c.provider.id == "claudeCli":
        args = [exe, "-p", "--output-format", "text"]
        if c.model:
            args += ["--model", c.model]
        return args
    args = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only"]
    if c.model:
        args += ["-m", c.model]
    return args + ["-"]


_CLI_ERRORS = ((re.compile(r"login|log in|authenticat|not logged|unauthori", re.I),
                "Вход в программу истёк — войдите заново (claude login / codex login)."),
               (re.compile(r"rate.?limit|usage limit|quota|limit reached", re.I),
                "Упёрлись в лимит подписки — подождите или выберите другого провайдера."))


def _kill_tree(proc: subprocess.Popen) -> None:
    """claude/codex в Windows — обёртка .cmd: убить нужно всё дерево, иначе сам агент продолжит работать."""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()


def _cli_chat(c: Config, system: str, user: str, timeout: float, cancel: threading.Event | None) -> str:
    prompt = ("Ты — молчаливый инструмент: выполни задание и выведи только результат, без вступлений.\n\n"
              + system + "\n\n" + user)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(_cli_args(c), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=flags, cwd=os.path.expanduser("~"))
    except OSError as e:
        raise AIError(f"Не удалось запустить {c.provider.name}: {e}") from None
    out: dict = {}

    def talk():
        try:
            out["o"], out["e"] = proc.communicate(prompt.encode("utf-8"))
        except Exception as e:   # noqa: BLE001 — любая беда общения с процессом
            out["x"] = e
    th = threading.Thread(target=talk, daemon=True)
    th.start()
    end = time.monotonic() + timeout
    while th.is_alive():
        th.join(0.2)
        if cancel is not None and cancel.is_set() or time.monotonic() > end:
            _kill_tree(proc)
            th.join(2)
            if cancel is not None and cancel.is_set():
                raise Cancelled("Отменено.")
            raise AIError("Нейросеть не ответила вовремя — попробуйте ещё раз.")
    text = (out.get("o") or b"").decode("utf-8", errors="replace")
    err = (out.get("e") or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0 or not text.strip():
        for rx, hint in _CLI_ERRORS:
            if rx.search(err) or rx.search(text):
                raise AIError(hint)
        raise AIError(f"{c.provider.name} завершился с ошибкой: {(err or text).strip()[:300]}")
    return _clean(text)


def chat(c: Config, system: str, user: str, timeout: float, cancel: threading.Event | None = None,
         json_mode: bool = True) -> str:
    """Один запрос к провайдеру. AIError — с понятной причиной."""
    why = problem(c)
    if why:
        raise AIError(why)
    if c.provider.kind == "cli":
        return _cli_chat(c, system, user, timeout, cancel)
    result: dict = {}

    def run():
        try:
            result["t"] = _openai_chat(c, system, user, timeout, json_mode)
        except Exception as e:   # noqa: BLE001
            result["x"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    while th.is_alive():
        th.join(0.2)
        if cancel is not None and cancel.is_set():
            raise Cancelled("Отменено.")   # запрос дойдёт сам — его ответ просто не нужен
    if "x" in result:
        raise result["x"] if isinstance(result["x"], AIError) else AIError(str(result["x"]))
    return result["t"]


def ask(settings, system: str, user: str, cancel: threading.Event | None = None,
        on_status=None) -> tuple[str, str]:
    """Запрос основному провайдеру, при ошибке — запасному. → (ответ, имя провайдера)."""
    main = config(settings)
    if main is None:
        raise AIError("Нейросеть не подключена — ⚙ Настройки → Нейросеть, или «Скопировать промпт».")
    n = len(system) + len(user)
    try:
        if on_status:
            on_status(f"Спрашиваю {main.provider.name}…")
        return chat(main, system, user, timeout_for(settings, n, main.provider.kind == "cli"), cancel), \
            main.provider.name
    except Cancelled:
        raise
    except AIError as e:
        back = config(settings, settings.ai_backup_provider) if settings.ai_backup_enabled else None
        if back is None or back.provider.id == main.provider.id:
            raise
        log.warning("Основной провайдер не справился (%s) — пробую запасной: %s", e, back.provider.name)
        if on_status:
            on_status(f"{main.provider.name} не ответил — спрашиваю запасного: {back.provider.name}…")
        try:
            return chat(back, system, user, timeout_for(settings, n, back.provider.kind == "cli"), cancel), \
                back.provider.name
        except AIError as e2:
            if isinstance(e2, Cancelled):
                raise
            raise AIError(f"Не ответили оба. {main.provider.name}: {e}  {back.provider.name}: {e2}") from None


def list_models(c: Config, timeout: float = 15) -> list[str]:
    """Модели сервера (GET /v1/models), без эмбеддингов и распознавания речи; у cli — свой список."""
    if c.provider.kind == "cli":
        return list(c.provider.models)
    why = problem(c)
    if why:
        raise AIError(why)
    url = chat_url(c.url).rsplit("/chat/completions", 1)[0] + "/models"
    data = _http_json(url, None, c.key, timeout)
    ids = [str(m.get("id")) for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    return sorted(i for i in ids if not re.search(r"embed|whisper|tts|rerank|moderation", i, re.I))


def check(c: Config, timeout: float = 60) -> tuple[int, str]:
    """Проверка связи: короткий запрос. → (миллисекунды, ответ). AIError — не вышло."""
    t0 = time.monotonic()
    text = chat(c, "Ответь одним словом.", "Скажи «готово».", timeout, json_mode=False)
    return int((time.monotonic() - t0) * 1000), text.strip()[:80]
