"""Имитация ручного набора: живой ритм, паузы на обдумывание, опечатки с исправлением.

Работает поверх готовых единиц печати (build_units) и только добавляет к ним:
  * k     — множитель задержки после символа: знакомые слова и частые сочетания букв
            быстрее, начало слова, Shift, цифры и знаки — медленнее, плюс медленный дрейф
            темпа. Среднее k по тексту = 1, поэтому заданная скорость сохраняется;
  * pause — пауза перед символом (обдумывание новой строки, заминка перед длинным именем);
  * опечатки — лишние «char»-единицы и «back» (Backspace). Их src_end равен длине уже
    верно набранного текста, поэтому шкала прогресса и пауза/продолжение работают как обычно.

Опечатка — как у человека: ошибся (соседняя клавиша, перестановка, удвоение, пропуск,
Shift не вовремя, задел соседнюю клавишу), по инерции набрал ещё 0–3 символа того же
слова, заметил (короткая пауза), быстро стёр и допечатал верно. Иногда стирает на одну
букву больше и набирает её заново.

Безопасность для IDE: ошибочный символ — всегда буква внутри слова (идентификатора),
всё набранное до исправления — символы этого же слова, Backspace не выходит за начало
слова и никогда не стирает первую букву строки. Скобки, кавычки, отступы, Enter и
служебные единицы не участвуют, поэтому автоскобки и автоотступ редактора не задеваются.
Итоговый текст всегда совпадает с образцом.
"""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import Settings
    from .typer import Unit

# слова, которые набирают «на автомате» — быстрее остальных и почти без опечаток
FAMILIAR = set("""
print return def self import from for in if else elif while True False None and or not range len
class input int str float list dict set append pass break continue with as try except lambda
const let var function console log new this null true false await async export default undefined
public static void string std include printf main package func fmt
the to of is it that you on are be was have
и в не на что это как с по для мы вы он она то так же но из у за от до если или все уже есть
""".split())
# слова, перед которыми человек задумывается (начало нового смыслового куска)
THINK_BEFORE = set("def class for while if elif else try except with return import from function async".split())

SHIFTED = set('~!@#$%^&*()_+{}|:"<>?')
CLOSERS = set(")]}")
OPENERS = set(":{([")          # строка открыла блок — следующую человек продумывает чуть дольше
SENTENCE_END = set(".!?")

# частые сочетания набираются «очередью» — чуть быстрее
BIGRAMS = set("""
th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io le ve co me
de hi ri ro ic ne ea ra ce li ch ll be ma si om ur ef rn pr
ст но то на ен ов ни ра во ко ро пр ал ре по ер ли от ос го ел та ет ол ть ин од ка ан ло те ск
""".split())
TRIGRAMS = set("""
the and ing ion tio ent ati for her ter hat tha ere ate his con res ver all ons nce men ith ted ers
pro thi wit are ess not ive was ect rea com eve per int est sta cti ica ist ear ain one our iti rat
ста ени ост ого ова про что как ния пре ать ный тор ски ель ого при
""".split())
FAST_PAIRS = {"()", "[]", "{}", '""', "''", "):", ");", "),", ", ", ": ", " =", "= ", "==", "!=",
              "+=", "-=", "->", "=>", "//", "**", "__", ". "}

# виды опечаток и их доли: соседняя клавиша, перестановка, удвоение, пропуск,
# Shift не вовремя, лишняя соседняя клавиша
W_NEAR, W_SWAP, W_DOUBLE, W_OMIT, W_CASE, W_EXTRA = 40, 20, 12, 15, 8, 5
TYPO_GAP = 8            # опечатки не ближе 8 символов друг к другу — иначе набор выглядит нарочитым
MAX_RATE = 10.0         # больше 10 на 100 символов — уже не человек, а помеха
OVERDELETE = 0.10       # иногда стирает на букву больше и набирает её заново
_CARRY = ((0, 1, 2, 3), (42, 32, 17, 9))    # сколько символов набрано по инерции, пока не заметил
_CARRY_OMIT = ((0, 1, 2), (30, 40, 30))     # пропуск буквы замечают чуть позже

# ряды клавиатуры без знаков; соседние ряды сдвинуты на полклавиши (как на настоящей клавиатуре)
_ROWS = (
    ("qwertyuiop", "asdfghjkl", "zxcvbnm"),
    ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю"),
)


def _neighbors() -> dict[str, str]:
    near: dict[str, str] = {}
    for rows in _ROWS:
        for r, row in enumerate(rows):
            for i, ch in enumerate(row):
                side = [row[j] for j in (i - 1, i + 1) if 0 <= j < len(row)]
                vert = []
                if r > 0:                                   # ряд выше сдвинут влево
                    vert += [rows[r - 1][j] for j in (i, i + 1) if j < len(rows[r - 1])]
                if r + 1 < len(rows):                       # ряд ниже сдвинут вправо
                    vert += [rows[r + 1][j] for j in (i - 1, i) if 0 <= j < len(rows[r + 1])]
                near[ch] = "".join(side * 2 + vert)         # промах по ряду — вдвое чаще
    return near


NEIGHBORS = _neighbors()


def _cased(c: str) -> bool:
    """Буква с честной парой заглавная/строчная (без ß, İ и т.п.)."""
    up, low = c.upper(), c.lower()
    return up != low and len(up) == 1 and len(low) == 1 and up.lower() == low


def _wrong_key(ch: str, rng: random.Random, avoid: str = "") -> str | None:
    near = [c for c in NEIGHBORS.get(ch.lower(), "") if c != avoid.lower()]
    if not near:
        return None
    c = rng.choice(near)
    return c.upper() if ch.isupper() else c


def _is_word_char(u: "Unit") -> bool:
    return u.kind == "char" and len(u.text) == 1 and (u.text.isalnum() or u.text == "_")


def _find_words(units: list["Unit"]) -> list[tuple[int, int, bool, str]]:
    """Слова — непрерывные отрезки букв/цифр/_ в единицах «char»: (начало, конец, первое на строке, текст)."""
    words = []
    line_has_text = False
    i, n = 0, len(units)
    while i < n:
        u = units[i]
        if _is_word_char(u):
            j = i + 1
            while j < n and _is_word_char(units[j]):
                j += 1
            words.append((i, j, not line_has_text, "".join(units[t].text for t in range(i, j))))
            line_has_text = True
            i = j
            continue
        if u.kind == "char":
            if not u.text.isspace():
                line_has_text = True
        elif u.kind not in ("fast", "tab"):
            line_has_text = False   # Enter, переход курсора (nav) и прочее — осторожно считаем новой строкой
        i += 1
    return words


def humanize(units: list["Unit"], settings: "Settings", rng: random.Random | None = None) -> list["Unit"]:
    from .typer import Unit

    rng = rng or random.Random()
    if not units:
        return units
    think = max(0.0, float(settings.think_pause_s or 0))
    rate = min(MAX_RATE, max(0.0, float(getattr(settings, "typos_per_100", 0) or 0)))
    words = _find_words(units)
    _rhythm(units, words, think, rng)
    if not rate or not words:
        return units

    ends = [u.src_end for u in units]
    plan = _plan(units, words, rate * (max(ends) - min(ends) + 1) / 100, rng)
    if not plan:
        return units
    notice = 0.7 + 0.3 * min(think, 4.5) / 1.5      # реакция немного зависит от «задумчивости»
    inserts: dict[int, list[Unit]] = {}
    for wi, p, kinds in plan:
        at, extra = _episode(units, words[wi], p, kinds, rng, notice, Unit)
        if extra:
            inserts.setdefault(at, []).extend(extra)
    out: list[Unit] = []
    for i, u in enumerate(units):
        if i in inserts:
            out.extend(inserts[i])
        out.append(u)
    out.extend(inserts.get(len(units), ()))
    return out


# ------------------------------------------------------------------ ритм

def _rhythm(units: list["Unit"], words, think: float, rng: random.Random) -> None:
    """Проставляет k (темп) и pause (обдумывание) печатным символам."""
    word_of = [-1] * len(units)
    for wi, (a, b, _, _) in enumerate(words):
        word_of[a:b] = [wi] * (b - a)

    touched: list = []
    line_start, prev_blank, line_has_text, first_line = True, True, False, True
    prev_line_end = ""      # последний значимый символ предыдущей непустой строки
    last_sig = ""           # последний непробельный символ текущей строки
    prev = prev2 = ""
    drift, phase = 0.0, rng.uniform(0, 2 * math.pi)
    for idx, u in enumerate(units):
        kind = u.kind
        if kind == "newline":
            prev_blank = not line_has_text
            if line_has_text:
                prev_line_end = last_sig
            line_start, line_has_text, first_line = True, False, False
            prev = prev2 = last_sig = ""
            continue
        if kind != "char":
            if kind not in ("fast", "tab", "back", "cleanup"):
                # переход в другое место (nav и т.п.) — как новый смысловой кусок
                line_start, line_has_text, prev_blank = True, False, True
                prev = prev2 = last_sig = ""
            continue
        ch = u.text
        if line_start and ch.isspace():
            continue            # отступ — без изменений
        if len(ch) != 1:
            prev2, prev = prev, " "     # таб посреди строки, развёрнутый в пробелы
            continue
        wi = word_of[idx]
        w = words[wi][3] if wi >= 0 else ""
        in_word = idx - words[wi][0] if wi >= 0 else -1
        at_word_start = in_word == 0
        familiar = w in FAMILIAR

        # --- пауза перед символом
        if think:
            pause = 0.0
            if line_start:
                if first_line:
                    pause = rng.uniform(0.1, 0.35) * think         # только начал — думать особо нечего
                else:
                    pause = rng.uniform(0.15, 0.45) * think
                    if prev_blank:
                        pause += rng.uniform(0.3, 0.7) * think     # новый смысловой кусок
                    elif prev_line_end in OPENERS:
                        pause += rng.uniform(0.0, 0.25) * think    # открыл блок — продумывает тело
                if w in THINK_BEFORE:
                    pause += rng.uniform(0.1, 0.4) * think
                if ch in CLOSERS:
                    pause *= 0.35                                   # закрывающая скобка — на автомате
            elif at_word_start:
                if len(w) >= 10 and not familiar and rng.random() < 0.18:
                    pause = rng.uniform(0.25, 0.7) * think          # длинное имя: вспоминает, как пишется
                elif prev == " " and last_sig in SENTENCE_END and ch.isupper() and rng.random() < 0.5:
                    pause = rng.uniform(0.1, 0.35) * think          # новое предложение в обычном тексте
                elif rng.random() < (0.01 if familiar else 0.04):
                    pause = rng.uniform(0.1, 0.4) * think           # заминка посреди строки
            if pause > u.pause:
                u.pause = pause

        # --- темп
        k = 1.0
        if familiar:
            k *= 0.7
        elif at_word_start:
            k *= 1.3
        elif in_word >= 2:
            k *= 0.95                   # внутри слова пальцы разгоняются
        if ch.isupper():
            k *= 1.1 if prev.isupper() else 1.35        # Shift уже зажат — почти без задержки
        elif ch in SHIFTED:
            k *= 1.35
        elif ch.isdigit():
            k *= 1.0 if prev.isdigit() else 1.15
        elif ch == " ":
            k *= 0.85                   # пробел — большим пальцем, быстро
        elif not ch.isalnum():
            k *= 1.2
        pair = prev + ch
        if ch == prev:
            k *= 0.8                    # повтор той же клавиши
        elif pair in FAST_PAIRS or pair.lower() in BIGRAMS:
            k *= 0.85                   # частое сочетание — «очередью»
        if (prev2 + pair).lower() in TRIGRAMS:
            k *= 0.92
        # медленный дрейф темпа: случайное блуждание (сосредоточенность/усталость) + плавная волна
        drift = max(-0.2, min(0.2, 0.98 * drift + rng.gauss(0, 0.016)))
        k *= math.exp(drift) * (1 + 0.06 * math.sin(phase + idx / 60))
        u.k = k
        touched.append(u)
        line_start, line_has_text = False, True
        prev2, prev = prev, ch
        if not ch.isspace():
            last_sig = ch
    if touched:
        # среднее k = 1: заданная скорость (симв/мин) сохраняется при любом тексте
        mean = sum(u.k for u in touched) / len(touched)
        for u in touched:
            u.k /= mean


# ------------------------------------------------------------------ опечатки

def _typo_kinds(s: str, p: int) -> list[tuple[str, int]]:
    """Какие опечатки возможны на букве s[p] слова s (с весами)."""
    c = s[p]
    if not c.isalpha():
        return []
    nxt = s[p + 1] if p + 1 < len(s) else ""
    kinds = []
    if p >= 1:              # первую букву слова человек почти не путает
        near = NEIGHBORS.get(c.lower(), "")
        if near:
            kinds.append(("near", W_NEAR))
            if any(e != nxt.lower() for e in near):
                kinds.append(("extra", W_EXTRA))
        if nxt != c:        # иначе «ошибка» совпала бы с верным текстом
            kinds.append(("double", W_DOUBLE))
            if nxt.isalpha():
                kinds.append(("swap", W_SWAP))
                kinds.append(("omit", W_OMIT))
    # Shift: забыл/нажал поздно (заглавная) или отпустил поздно (строчная после заглавной)
    if _cased(c) and (c.isupper() or (p >= 1 and s[p - 1].isupper() and _cased(s[p - 1]))):
        kinds.append(("case", W_CASE))
    return kinds


def _plan(units: list["Unit"], words, target: float, rng: random.Random) -> list[tuple[int, int, list]]:
    """Где будут опечатки: в среднем target штук, не ближе TYPO_GAP символов друг к другу."""
    cands = []      # (позиция в тексте, вес, номер слова, буква в слове, виды)
    for wi, (a, b, line_first, s) in enumerate(words):
        if b - a < 2:
            continue
        base = 0.5 if s in FAMILIAR else (1.2 if b - a >= 7 else 1.0)
        # первая буква — только «Shift не вовремя» и никогда в начале строки
        for p in range(1 if line_first else 0, b - a):
            kinds = _typo_kinds(s, p)
            if kinds:
                cands.append((units[a + p].src_end - 1, base * (0.5 if p == 0 else 1.0), wi, p, kinds))
    if not cands or target <= 0:
        return []
    pos = [c[0] for c in cands]
    wts = [c[1] for c in cands]
    scale = _calibrate(pos, wts, target)
    plan = []
    free_from = -TYPO_GAP
    for s, w, wi, p, kinds in cands:
        if s >= free_from and rng.random() < scale * w:
            plan.append((wi, p, kinds))
            free_from = s + TYPO_GAP
    return plan


def _expected(pos: list[int], wts: list[float], scale: float) -> float:
    """Точное ожидаемое число опечаток при вероятностях min(1, scale·w) и запрете ближе TYPO_GAP.

    В окне короче TYPO_GAP опечаток не больше одной, поэтому события «опечатка в j»
    внутри окна несовместны: P(место свободно) = 1 − сумма их вероятностей.
    """
    probs: list[float] = []
    window = total = 0.0
    lo = 0
    for i, s in enumerate(pos):
        while pos[lo] <= s - TYPO_GAP:
            window -= probs[lo]
            lo += 1
        pr = min(1.0, scale * wts[i]) * max(0.0, 1.0 - window)
        probs.append(pr)
        window += pr
        total += pr
    return total


def _calibrate(pos: list[int], wts: list[float], target: float) -> float:
    """Подбирает множитель вероятности, чтобы ожидаемое число опечаток было равно target."""
    scale = target / sum(wts)
    for _ in range(12):
        got = _expected(pos, wts, scale)
        if got <= 0 or abs(got - target) <= 0.002 * target or scale * min(wts) >= 1:
            break       # попали в цель (или текст короткий и больше опечаток не вместить)
        scale *= target / got
    return scale


def _episode(units: list["Unit"], word, p: int, kinds, rng: random.Random, notice: float, Unit):
    """Опечатка в слове: ошибка → 0–3 символа по инерции → заметил → Backspace → верно.

    Возвращает (индекс единицы, перед которой вставить, вставляемые единицы). Исходные
    единицы слова остаются на месте — они и есть «правильный» набор после исправления.
    """
    a, b, _, s = word
    m = b - a
    letters = units[a:b]
    names = [kd for kd, _ in kinds]
    kind = rng.choices(names, [wt for _, wt in kinds])[0]
    c = s[p]
    carry_w = _CARRY
    # typed — что набрано вместо верного текста, начиная с буквы d (где текст разошёлся)
    if kind == "near":
        d, typed, nxt = p, [(_wrong_key(c, rng), letters[p].k * rng.uniform(0.8, 1.0))], p + 1
    elif kind == "swap":
        d, typed, nxt = p, [(s[p + 1], letters[p].k * rng.uniform(0.7, 0.9)), (c, letters[p + 1].k)], p + 2
    elif kind == "double":      # клавиша «дребезжит»: вторая буква сразу за первой
        d, typed, nxt = p + 1, [(c, letters[p].k * rng.uniform(0.5, 0.7))], p + 1
    elif kind == "omit":        # пальцы проскочили букву
        d, typed, nxt = p, [(s[p + 1], letters[p].k * rng.uniform(0.8, 1.0))], p + 2
        carry_w = _CARRY_OMIT
    elif kind == "extra":       # задел соседнюю клавишу вместе с нужной
        avoid = s[p + 1] if p + 1 < m else ""
        d, typed, nxt = p + 1, [(_wrong_key(c, rng, avoid), letters[p].k * rng.uniform(0.4, 0.6))], p + 1
    elif c.isupper():           # Shift забыт или нажат поздно: «hello»/«hEllo» вместо «Hello»
        d, typed, nxt = p, [(c.lower(), letters[p].k)], p + 1
        if nxt < m and _cased(s[nxt]) and s[nxt].islower() and rng.random() < 0.5:
            typed.append((s[nxt].upper(), letters[nxt].k * 1.2))
            nxt += 1
    else:                       # Shift отпущен поздно: «HEllo» вместо «Hello»
        d, typed, nxt = p, [(c.upper(), letters[p].k * 1.2)], p + 1
    if typed[0][0] is None:
        return a, []
    carry = min(rng.choices(*carry_w)[0], m - nxt)          # успел набрать дальше — только своё слово
    typed += [(s[t], letters[t].k) for t in range(nxt, nxt + carry)]

    src = letters[0].src_end - 1 + d        # столько символов образца набрано верно
    out = [Unit(kind="char", text=ch, src_end=src, k=k) for ch, k in typed]
    if d < m and letters[d].pause:          # заминка перед буквой — до ошибки, а не после исправления
        out[0].pause, letters[d].pause = letters[d].pause, 0.0
    over = d >= 2 and rng.random() < OVERDELETE      # первая буква слова остаётся всегда
    nback = len(typed) + over
    bk = rng.uniform(0.55, 0.7)                       # стирает чуть быстрее, чем печатает
    for j in range(nback):
        last = j == nback - 1
        out.append(Unit(kind="back", text="", src_end=src - 1 if over and last else src,
                        k=bk * rng.uniform(0.93, 1.07) * (1.3 if last else 1.0),
                        pause=_notice(rng, carry) * notice if j == 0 else 0.0))
    if over:
        out.append(Unit(kind="char", text=s[d - 1], src_end=src, k=letters[d - 1].k))
    return a + d, out


def _notice(rng: random.Random, carry: int) -> float:
    """Пауза «заметил ошибку»: сразу после неё — короче, через пару букв — дольше."""
    return rng.uniform(0.15, 0.45) if carry == 0 else rng.uniform(0.25, 0.7)
