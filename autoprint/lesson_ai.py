"""Занятие от нейросети: промпт, формат ответа (JSON) и разбор ответа в занятие.

Путей два, результат один:
- «Скопировать промпт» → вставить в свой чат (ChatGPT, Claude, DeepSeek…) → ответ вставить обратно в «Импорт»;
- подключённая нейросеть (⚙ → Нейросеть) — тот же промпт уходит по API, ответ разбирается сам.

Как разложить материал (STYLES):
- «С комментариями» — код целиком, в нём комментарии для преподавателя: что сказать перед каждым куском;
- «По шагам» — то же, плюс номер шага у каждой строки (печать по шагам, с возвратом наверх);
- «По частям» — несколько блоков кода, между ними пояснения.

Формат ответа (FORMAT):
{
  "format": "autoprintcode-lesson", "version": 1,
  "title": "Название занятия",
  "blocks": [
    {"type": "task" | "explain" | "hint" | "text", "text": "Markdown"},
    {"type": "code", "lang": "python", "code": "код с комментариями"},
    {"type": "code", "lang": "python", "lines": [[1, "строка"], [1, "строка"], [3, "import …"], …]}
  ]
}
"lines" — строки итогового файла по порядку, у каждой номер шага, на котором она появляется: шаг может
дописать строки выше уже напечатанных (import в начало). Так число строк и шагов не может разойтись.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

from .comments import strip_comments
from .storage import BLOCK_CODE, BLOCK_MARKDOWN, ROLES, Block, Template

FORMAT = "autoprintcode-lesson"

STYLE_COMMENTS, STYLE_STEPS, STYLE_PARTS = "comments", "steps", "parts"
STYLES = {
    STYLE_COMMENTS: "С комментариями",
    STYLE_STEPS: "По шагам",
    STYLE_PARTS: "По частям",
}
DETAIL_SHORT, DETAIL_FULL = "short", "full"


@dataclass
class Options:
    style: str = STYLE_STEPS
    task: bool = True            # краткое условие задачи в начале занятия
    explain: bool = True         # пояснения для учеников (отдельные текстовые блоки)
    keep_code: bool = True       # код не менять — только комментарии и разметка
    detail: str = DETAIL_SHORT   # комментарии: коротко | подробно
    lang: str = ""               # язык кода, если известен ("" — пусть определит нейросеть)


# ---------------------------------------------------------------- промпт

_STYLE_RULES = {
    STYLE_COMMENTS: """\
Разложи в ОДИН блок кода ("type": "code" с полем "code").
В коде перед каждым смысловым куском — строка-комментарий для преподавателя: что сказать ученикам
перед тем, как этот кусок появится. Хвостовые комментарии (после кода на той же строке) — только
если очень коротко поясняют неочевидное место.""",
    STYLE_STEPS: """\
Разложи в ОДИН блок кода с ПОШАГОВОЙ разметкой ("type": "code" с полем "lines").
"lines" — все строки итогового файла по порядку, у каждой номер шага: [номер_шага, "текст строки"].
Шаги — как преподаватель пишет программу у доски: сначала простое и данные, потом обработка, потом
вывод. Шаг может дописывать строки ВЫШЕ уже написанного: например, import, который понадобился на
шаге 3, стоит первой строкой файла, но с номером 3. Шагов обычно 3–8, в шаге 1–6 строк кода.
Каждый шаг начинается со строки-комментария для преподавателя — что сказать перед этим шагом
(у неё тот же номер шага). Пустые строки — с номером шага соседнего кода.""",
    STYLE_PARTS: """\
Разложи на ЧАСТИ: чередуй блоки "explain" (что делаем в этой части, 1–3 предложения для учеников)
и "code" (кусок программы с полем "code"). Части по порядку дают всю программу целиком.
Обычно 2–6 частей. В коде каждой части — строки-комментарии для преподавателя: что сказать.""",
}


def build_prompt(material: str, opt: Options) -> str:
    """Промпт целиком для «Скопировать промпт»: правила, формат, пример — и материал пользователя в конце."""
    return instruction(opt) + material_message(material)


def material_message(material: str) -> str:
    return f"\nМАТЕРИАЛ (код или задание с решением):\n<<<\n{material.strip()}\n>>>\n"


def instruction(opt: Options) -> str:
    """Правила, формат и пример — без материала (по API это системное сообщение, материал — отдельным)."""
    keep = ("Код НЕ МЕНЯЙ: ни имён, ни логики, ни порядка, ни форматирования — только добавь комментарии "
            "(и разметку шагов/частей). Если в материале есть условие задачи — его не переписывай в код."
            if opt.keep_code else
            "Код можно поправить, если в нём ошибка или он явно неудобен для объяснения новичкам, — но "
            "без лишнего усложнения.")
    detail = ("Комментарии короткие: одна строка, по сути, разговорным языком."
              if opt.detail == DETAIL_SHORT else
              "Комментарии подробные: 1–3 строки, объясняй «зачем», а не только «что», разговорным языком.")
    extra = []
    if opt.task:
        extra.append('Первым блоком — краткое условие задачи ("type": "task", Markdown: заголовок «## …» и '
                     '1–4 строки). Если условия в материале нет — сформулируй его по коду.')
    if opt.explain and opt.style != STYLE_PARTS:
        extra.append('Перед кодом — блок "explain": 1–3 предложения для учеников, что будем делать.')
    if not opt.explain and opt.style != STYLE_PARTS:
        extra.append('Блоков "explain" не добавляй.')
    lang = f'Язык кода: {opt.lang}.' if opt.lang else 'Язык кода определи сам ("lang": python, javascript, …).'
    example = _EXAMPLES[opt.style]
    return f"""Ты помогаешь преподавателю программирования подготовить занятие для программы AutoPrintCode.
Программа сама «печатает» код в редактор во время урока, а преподаватель в это время объясняет.
Комментарии в коде — это подсказки преподавателю: программа может не печатать их, а показывать
на суфлёре — что сказать перед следующим куском. Пиши комментарии на русском, как живую речь
преподавателя, обращаясь к ученикам («сейчас заведём…», «обратите внимание…»).

{_STYLE_RULES[opt.style]}

{keep}
{detail}
{lang}
{chr(10).join(extra)}

ОТВЕТ — ТОЛЬКО JSON, без пояснений до и после, по формату:
{{
  "format": "{FORMAT}", "version": 1,
  "title": "короткое название занятия",
  "blocks": [ ... ]
}}
Типы блоков: "task" (условие), "explain" (пояснение), "hint" (подсказка), "text" (просто текст) — с полем
"text" (Markdown); "code" — с полем "lang" и либо "code" (строка), либо "lines" (пошаговая разметка).
Внутри строк JSON экранируй кавычки и переводы строк как положено (\\" и \\n).

Пример ответа (по смыслу, не копируй):
{example}
"""


_EXAMPLES = {
    STYLE_COMMENTS: """{"format": "autoprintcode-lesson", "version": 1, "title": "Сумма списка",
 "blocks": [
  {"type": "task", "text": "## Сумма чисел\\nНапишите функцию total(nums) без sum()."},
  {"type": "code", "lang": "python", "code": "# Заводим функцию, которая принимает список\\ndef total(nums):\\n    # Копим сумму в переменной, начинаем с нуля\\n    result = 0\\n    for n in nums:\\n        result += n\\n    return result"}
 ]}""",
    STYLE_STEPS: """{"format": "autoprintcode-lesson", "version": 1, "title": "Оценки",
 "blocks": [
  {"type": "explain", "text": "Посчитаем средний балл и медиану."},
  {"type": "code", "lang": "python", "lines": [
    [3, "import statistics"], [3, ""],
    [1, "# Сначала — сами оценки"], [1, "grades = [5, 4, 3, 5]"],
    [2, "# Средний балл: сумму делим на количество"], [2, "average = sum(grades) / len(grades)"],
    [3, "# Медиану возьмём из модуля statistics — допишем import наверх"],
    [3, "median = statistics.median(grades)"]
  ]}
 ]}""",
    STYLE_PARTS: """{"format": "autoprintcode-lesson", "version": 1, "title": "Чётные числа",
 "blocks": [
  {"type": "explain", "text": "Сначала заведём список чисел."},
  {"type": "code", "lang": "python", "code": "# Список, с которым будем работать\\nnums = [1, 2, 3, 4]"},
  {"type": "explain", "text": "Теперь отберём чётные."},
  {"type": "code", "lang": "python", "code": "# Остаток от деления на 2 равен нулю — число чётное\\nevens = [n for n in nums if n % 2 == 0]"}
 ]}""",
}


# ---------------------------------------------------------------- разбор ответа

class LessonError(ValueError):
    """Понятная пользователю ошибка разбора ответа."""


@dataclass
class Parsed:
    template: Template
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        t = self.template
        codes = t.code_blocks()
        steps = sum(len(set(b.steps)) for b in codes if b.steps and len(set(b.steps)) > 1)
        parts = [f"блоков: {len(t.blocks)}", f"кода: {len(codes)}"]
        if steps:
            parts.append(f"шагов: {steps}")
        return " · ".join(parts)


_TYPE_ROLE = {"task": "task", "explain": "explain", "hint": "hint", "text": "text", "markdown": "text",
              "условие": "task", "пояснение": "explain", "подсказка": "hint"}


def extract_json(text: str):
    """JSON из ответа нейросети: без ```-ограды и пояснений вокруг. None — JSON нет."""
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if fence:
        s = fence.group(1)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    body = s[a:b + 1]
    for candidate in (body, re.sub(r",\s*([}\]])", r"\1", body)):   # лишние запятые перед } и ]
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def is_lesson_json(text: str) -> bool:
    data = extract_json(text)
    return isinstance(data, dict) and (data.get("format") == FORMAT or isinstance(data.get("blocks"), list))


def _compact_steps(steps: list[int]) -> list[int]:
    """Номера шагов подряд с 1, порядок сохраняется (3, 7, 7, 9 → 1, 2, 2, 3)."""
    order = {s: i + 1 for i, s in enumerate(sorted(set(steps)))}
    return [order[s] for s in steps]


def _lines(raw, n_block: int, warnings: list[str]) -> tuple[str, list[int]]:
    texts, steps = [], []
    for item in raw:
        if isinstance(item, dict):
            st, tx = item.get("step", item.get("s", 1)), item.get("text", item.get("t", ""))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            st, tx = item
        else:
            raise LessonError(f"Блок кода {n_block}: строка разметки шагов не в формате [шаг, \"текст\"].")
        try:
            st = max(1, int(st))
        except (TypeError, ValueError):
            st = steps[-1] if steps else 1
        texts.append(str(tx).replace("\r", "").replace("\n", " "))
        steps.append(st)
    if not texts:
        raise LessonError(f"Блок кода {n_block}: пустая разметка шагов.")
    compact = _compact_steps(steps)
    if compact != steps:
        warnings.append(f"Блок кода {n_block}: номера шагов выровнены по порядку (1, 2, 3…).")
    return "\n".join(texts), compact


def parse_lesson(text: str, title: str = "") -> Parsed:
    """Ответ нейросети (или наш JSON) → занятие. LessonError — если разобрать нельзя."""
    data = extract_json(text)
    if data is None:
        raise LessonError("В ответе не нашёлся JSON. Попросите нейросеть ответить строго в формате из промпта "
                          "(без пояснений), или вставьте код во вкладке «Вставить».")
    if isinstance(data, dict) and isinstance(data.get("autoprintcode_template"), dict):
        data = data["autoprintcode_template"]          # наш экспорт занятия в .json
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        raise LessonError("В JSON нет списка блоков (\"blocks\").")
    warnings: list[str] = []
    blocks: list[Block] = []
    n_code = 0
    for i, raw in enumerate(data["blocks"], 1):
        if not isinstance(raw, dict):
            warnings.append(f"Блок {i} пропущен: это не объект.")
            continue
        kind = str(raw.get("type", "")).lower()
        if kind == "code" or "code" in raw or "lines" in raw:
            n_code += 1
            lang = str(raw.get("lang") or "python").lower()
            if isinstance(raw.get("lines"), list):
                code, steps = _lines(raw["lines"], n_code, warnings)
            else:
                code = str(raw.get("code") or raw.get("text") or "").replace("\r\n", "\n").strip("\n")
                steps = []
                if isinstance(raw.get("steps"), list):
                    try:
                        steps = [max(1, int(s)) for s in raw["steps"]]
                    except (TypeError, ValueError):
                        steps = []
            if not code.strip():
                warnings.append(f"Блок кода {n_code} пустой — пропущен.")
                continue
            blocks.append(Block(BLOCK_CODE, code, lang=lang, steps=steps, title=str(raw.get("title") or "")))
        else:
            role = _TYPE_ROLE.get(kind, "text")
            if kind and kind not in _TYPE_ROLE:
                warnings.append(f"Блок {i}: неизвестный тип «{kind}» — стал обычным текстом.")
            body = str(raw.get("text") or "").replace("\r\n", "\n").strip()
            if body:
                blocks.append(Block(BLOCK_MARKDOWN, body, role=role if role in ROLES else "text"))
    if not any(b.type == BLOCK_CODE for b in blocks):
        raise LessonError("В ответе нет ни одного блока кода.")
    name = str(data.get("title") or title or "Новое занятие").strip()[:120]
    t = Template(title=name, blocks=blocks)
    t.active_block = next(b.id for b in blocks if b.type == BLOCK_CODE)
    return Parsed(t, warnings)


def _norm_code(text: str, lang: str) -> list[str]:
    """Код без комментариев и пустых строк, без хвостовых пробелов — для сравнения «код не меняли»."""
    bare = strip_comments(text, lang)[0]
    return [ln.rstrip() for ln in bare.split("\n") if ln.strip()]


def code_changes(original: str, parsed: Parsed) -> list[str]:
    """Чем код занятия отличается от исходного (без учёта комментариев и пустых строк). [] — не менялся."""
    codes = parsed.template.code_blocks()
    if not codes or not original.strip():
        return []
    lang = codes[0].lang
    got = [ln for b in codes for ln in _norm_code(b.text, b.lang)]
    want = _norm_code(original, lang)
    if got == want:
        return []
    # в материале могло быть условие задачи — сравниваем только строки, похожие на код из ответа
    if set(got) <= set(want):
        return []
    diff = [ln for ln in difflib.unified_diff(want, got, lineterm="", n=0) if ln[:1] in "+-"
            and not ln.startswith(("+++", "---"))]
    return diff[:12]
