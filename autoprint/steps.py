"""Печать по шагам — режим преподавателя.

У каждой строки блока кода есть номер шага (Block.steps). Готовая программа — все строки в исходном
порядке; шаг k добавляет свои строки к уже напечатанным шагам 1…k−1 — в том числе выше и между ними
(«вернулись наверх и дописали импорт»). Менять уже напечатанные строки шаги не умеют, только добавлять.

К месту вставки программа ведёт курсор сама: Ctrl+Home → ↓ × номер строки → End → Enter → строки шага
(или с конца: Ctrl+End → ↑ …). Поэтому код блока должен начинаться с первой строки документа — новый
файл, отдельная ячейка Jupyter, — а перенос длинных строк в редакторе должен быть выключен
(иначе ↓ идёт по экранным строкам). Первый шаг печатается там, где стоит курсор.

С «Без комментариев» строки-комментарии шага не печатаются: это текст для суфлёра — что сказать
перед шагом. Их номер шага тоже важен: комментарий относится к тому шагу, где стоит.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from .comments import comment_spans, comment_text, strip_comments
from .storage import Settings
from .typer import Unit, build_units


@dataclass
class SrcLine:
    index: int                  # номер строки в блоке
    start: int                  # позиция начала строки в тексте блока
    text: str                   # исходный текст строки
    step: int
    typed: str | None           # что печатается (без комментариев); None — строка не печатается
    char_map: list[int] = field(default_factory=list)   # позиция в тексте блока каждого символа typed


@dataclass
class Chunk:
    anchor: int                 # сколько строк документа выше места вставки (0 — в самый верх)
    lines: list[SrcLine]


def norm_steps(steps: list[int], n_lines: int) -> list[int]:
    """Шаги ровно по числу строк: недостающие — как у последней размеченной (или 1)."""
    out = [max(1, int(s)) for s in steps[:n_lines]]
    fill = out[-1] if out else 1
    return out + [fill] * (n_lines - len(out))


def step_numbers(steps: list[int], n_lines: int) -> list[int]:
    """Номера шагов, которые есть в блоке, по возрастанию (пропуски в нумерации допустимы)."""
    return sorted(set(norm_steps(steps, n_lines))) or [1]


def analyze(text: str, steps: list[int], lang: str, strip: bool) -> list[SrcLine]:
    lines = text.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    st = norm_steps(steps, len(lines))
    out = [SrcLine(i, starts[i], ln, st[i], None) for i, ln in enumerate(lines)]
    if not strip:
        for sl in out:
            sl.typed = sl.text
            sl.char_map = list(range(sl.start, sl.start + len(sl.text)))
        return out
    result, omap = strip_comments(text, lang)
    # строки результата → строки исходника: по первому символу строки или по «\n» перед ней
    r_chars: list[list[int]] = [[]]
    r_nl: list[int] = [-1]
    for ch, src in zip(result, omap):
        if ch == "\n":
            r_chars.append([])
            r_nl.append(src)
        else:
            r_chars[-1].append(src)
    for chars, nl in zip(r_chars, r_nl):
        anchor = chars[0] if chars else nl + 1
        li = max(0, bisect_right(starts, anchor) - 1)
        if out[li].typed is None:
            out[li].typed = "".join(text[i] for i in chars)
            out[li].char_map = chars
    return out


def plan(lines: list[SrcLine], k: int) -> list[Chunk]:
    """Куски шага k: подряд идущие (в документе) строки шага и место вставки каждого."""
    chunks: list[Chunk] = []
    before = 0
    cur: Chunk | None = None
    for sl in lines:
        if sl.typed is None or sl.step > k:
            continue        # не печатается или появится позже — в документе её нет, кусок не рвёт
        if sl.step < k:
            before += 1
            cur = None
        else:
            if cur is None:
                cur = Chunk(before, [])
                chunks.append(cur)
            cur.lines.append(sl)
            before += 1
    return chunks


def present_before(lines: list[SrcLine], k: int) -> int:
    """Сколько строк уже в документе к началу шага k."""
    return sum(1 for sl in lines if sl.typed is not None and sl.step < k)


def _nav(name: str, src: int) -> Unit:
    return Unit("nav", name, src)


def build_step_units(lines: list[SrcLine], k: int, settings: Settings) -> list[Unit]:
    """Единицы печати шага k: переходы к местам вставки + строки. src_end — позиции в тексте блока."""
    units: list[Unit] = []
    present = present_before(lines, k)
    for ch in plan(lines, k):
        text = "\n".join(sl.typed for sl in ch.lines)
        cmap: list[int] = []
        for i, sl in enumerate(ch.lines):
            if i:
                cmap.append(sl.start - 1)        # «\n» перед строкой в исходнике
            cmap.extend(sl.char_map)
        first_src = cmap[0] if cmap else ch.lines[0].start
        if present:   # в документе уже есть строки — идём к месту вставки
            if ch.anchor == 0:
                # в самый верх: новая пустая первая строка (Enter в начале документа, затем ↑)
                units += [_nav("ctrl_home", first_src), _nav("enter_raw", first_src), _nav("up", first_src)]
            else:
                target = ch.anchor - 1           # после этой строки (с нуля)
                if target <= (present - 1) / 2:
                    units.append(_nav("ctrl_home", first_src))
                    units += [_nav("down", first_src) for _ in range(target)]
                else:
                    units.append(_nav("ctrl_end", first_src))
                    units += [_nav("up", first_src) for _ in range(present - 1 - target)]
                units.append(_nav("end", first_src))
                units.append(Unit("newline", "\n", first_src))
        cu = build_units(text, settings, cleanup=True)
        for u in cu:
            if cmap:
                u.src_end = cmap[min(u.src_end, len(cmap)) - 1] + 1 if u.src_end > 0 else cmap[0]
        units += cu
        present += len(ch.lines)
    return units


def narration(text: str, lines: list[SrcLine], k: int, lang: str) -> list[str]:
    """Комментарии шага k — что сказать перед ним (для суфлёра)."""
    out: list[str] = []
    starts = [sl.start for sl in lines]
    for a, b in comment_spans(text, lang):
        li = max(0, bisect_right(starts, a) - 1)
        if lines[li].step == k:
            t = comment_text(text[a:b])
            if t and (not out or out[-1] != t):
                out.append(t)
    return out


def preview(lines: list[SrcLine], k: int, limit: int = 8) -> tuple[list[str], int]:
    """Код шага k для суфлёра: куски в разных местах файла разделены «⋯», пустые строки по краям
    кусков убраны. → (строки для показа, сколько строк кода не поместилось)."""
    out: list[str] = []
    shown = hidden = 0
    for ch in plan(lines, k):
        rows = [sl.typed for sl in ch.lines]
        while rows and not rows[0].strip():
            rows.pop(0)
        while rows and not rows[-1].strip():
            rows.pop()
        if not rows:
            continue
        if out and shown < limit:
            out.append("⋯")
        for r in rows:
            if shown < limit:
                out.append(r)
                shown += 1
            else:
                hidden += 1
    return out, hidden


def auto_by_comments(text: str, lang: str) -> list[int]:
    """Разметка «по комментариям»: каждая строка-комментарий (после кода) начинает новый шаг."""
    lines = text.split("\n")
    spans = comment_spans(text, lang)
    comment_start_lines = set()
    pos = 0
    starts = []
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    for a, _b in spans:
        li = max(0, bisect_right(starts, a) - 1)
        if not lines[li][:a - starts[li]].strip():      # комментарий с начала строки, а не в хвосте
            comment_start_lines.add(li)
    steps, step, seen_code = [], 1, False
    for i, ln in enumerate(lines):
        if i in comment_start_lines and seen_code and (i == 0 or i - 1 not in comment_start_lines):
            step += 1
            seen_code = False
        if ln.strip() and i not in comment_start_lines:
            seen_code = True
        steps.append(step)
    return _compact(_blank_lines_follow(lines, steps))


def auto_by_blank_lines(text: str) -> list[int]:
    """Разметка «по пустым строкам»: каждый абзац кода — свой шаг."""
    lines = text.split("\n")
    steps, step, prev_blank, seen = [], 1, False, False
    for ln in lines:
        blank = not ln.strip()
        if not blank and prev_blank and seen:
            step += 1
        if not blank:
            seen = True
        steps.append(step)
        prev_blank = blank
    return _compact(_blank_lines_follow(lines, steps))


def _blank_lines_follow(lines: list[str], steps: list[int]) -> list[int]:
    """Пустые строки между кусками — к следующему шагу (отступ появляется вместе с новым куском)."""
    out = list(steps)
    for i in range(len(lines) - 2, -1, -1):
        if not lines[i].strip():
            out[i] = out[i + 1]
    return out


def _compact(steps: list[int]) -> list[int]:
    """Номера шагов подряд с 1."""
    order = {s: i + 1 for i, s in enumerate(sorted(set(steps)))}
    return [order[s] for s in steps]
