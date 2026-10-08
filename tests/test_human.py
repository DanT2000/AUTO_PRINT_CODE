"""Проверка режима «Как человек» (autoprint/human.py) без нажатия клавиш.

    python tests/test_human.py

Единицы печати собираются настоящим build_units, затем «печатаются» в строковый буфер
(char — вставка, back — Backspace, newline — Enter, fast/tab — отступ, cleanup/nav — ничего).
Проверяется: итог совпадает с образцом, опечаток в среднем столько, сколько задано,
опечатки не трогают скобки/кавычки/отступы/Enter, src_end не убегает вперёд; ритм идёт
очередями (слово быстро и ровно, разброс — в промежутках между словами), среднее k ≈ 1.
Рабочая папка data/ не используется.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(errors="replace")    # консоль cp1251 не знает «→», «≈» и т.п.
except (AttributeError, ValueError):
    pass
os.environ["AUTOPRINT_DATA"] = tempfile.mkdtemp(prefix="autoprint-human-")

from autoprint import typer  # noqa: E402
from autoprint.human import humanize  # noqa: E402
from autoprint.storage import PROFILE_IDE, PROFILE_PLAIN, Settings  # noqa: E402

PY = '''import os
from pathlib import Path


class ReportBuilder:
    """Собирает отчёт о продажах: строки, итоги, экспорт."""

    def __init__(self, title: str, items: list[dict]) -> None:
        self.title = title
        self.items = items or []
        self._cache = {}

    def total(self) -> float:
        return sum(item["price"] * item.get("qty", 1) for item in self.items)

    def render(self) -> str:
        lines = [f"Отчёт: {self.title!r}", "=" * 40]
        for idx, item in enumerate(self.items, start=1):
            name = item.get('name', "без названия")
            lines.append(f"{idx:>3}. {name:<20} {item['price']:8.2f}")
        lines.append(f"Итого: {self.total():.2f} ({len(self.items)} позиций)")
        return "\\n".join(lines)


def main():
    builder = ReportBuilder("Квартальный отчёт", [{"name": "Клавиатура", "price": 2490.0, "qty": 2}])
    if builder.total() > 1000 and not os.environ.get("QUIET"):
        print(builder.render())
    else:
        print('nothing to show')  # комментарий: HelloWorld, getValue, HTTPServer


if __name__ == "__main__":
    main()
'''

JS = '''const express = require("express");
const app = express();

function calculateDiscount(price, percent = 10) {
  if (typeof price !== "number" || price < 0) {
    throw new TypeError(`Invalid price: ${price}`);
  }
  return Math.round(price * (100 - percent)) / 100;
}

app.get("/api/products/:id", async (req, res) => {
  const product = await db.findProductById(req.params.id);
  res.json({ ...product, discounted: calculateDiscount(product.price) });
});

app.listen(3000, () => console.log('Server listening on http://localhost:3000'));
'''

PROSE = '''Hello everyone! Today we are going to write a small program together. First, we create
a new file and import the libraries we need. Then we define a function that reads the data,
cleans it and prints a short summary. It sounds simple, but there are several details worth
discussing along the way, especially error handling and naming.

Привет! Сегодня мы напишем небольшую программу. Сначала создадим новый файл и подключим
нужные библиотеки. Затем опишем функцию, которая читает данные, очищает их и печатает
короткую сводку. Звучит просто, но по дороге стоит обсудить несколько деталей.
'''

TABBED = "def f(x):\n\tif x:\n\t\treturn 'a\tb'\n\treturn None\n"

SHORT = ["x = 1", "print('hi')", "ok", "", "a\nb", "HelloWorld()"]

WORDCH = set("_")
fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    if cond:
        print(f"OK   {name}")
    else:
        fails += 1
        print(f"FAIL {name}" + (f": {detail}" if detail else ""))


def make_settings(**kw) -> Settings:
    s = Settings()
    s.human_typing = True
    s.typos_per_100 = 1.5
    s.think_pause_s = 1.5
    s.profile = PROFILE_IDE
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def build(text: str, s: Settings, seed: int) -> list:
    """build_units с детерминированным генератором для humanize."""
    rng = random.Random(seed)
    orig = typer.humanize
    typer.humanize = lambda units, settings: orig(units, settings, rng)
    try:
        return typer.build_units(text, s)
    finally:
        typer.humanize = orig


def is_word(c: str) -> bool:
    return c.isalnum() or c in WORDCH


class Sim:
    """Буфер редактора: печатает единицы и следит за безопасностью опечаток."""

    def __init__(self, src: str, s: Settings) -> None:
        tab = " " * max(1, s.tab_width)
        self.tab = tab
        self.src = src
        self.expected = src.replace("\t", tab)
        # позиция в образце → позиция в развёрнутом тексте (табы → пробелы)
        self.map = [0]
        for ch in src:
            self.map.append(self.map[-1] + (len(tab) if ch == "\t" else 1))
        self.buf: list[str] = []
        self.match = 0          # длина верного префикса буфера
        self.typos = 0
        self.overdeletes = 0
        self.kinds: dict[str, int] = {}
        self.starts: list[int] = []          # где начиналась каждая опечатка (позиция в тексте)
        self.errors: list[str] = []

    def err(self, i: int, msg: str) -> None:
        if len(self.errors) < 5:
            ctx = "".join(self.buf[-30:]).replace("\n", "⏎")
            self.errors.append(f"#{i}: {msg} | …{ctx}")

    def diverged(self) -> bool:
        return self.match < len(self.buf)

    def cur_line(self) -> str:
        text = "".join(self.buf[-400:])
        return text[text.rfind("\n") + 1:]

    def put(self, c: str) -> None:
        if self.match == len(self.buf) and self.match < len(self.expected) and self.expected[self.match] == c:
            self.match += 1
        self.buf.append(c)

    def classify(self, wrong: str) -> str:
        pos = self.match
        exp = self.expected
        right = exp[pos] if pos < len(exp) else ""
        if right and wrong != right and wrong.lower() == right.lower():
            return "case"
        if pos + 1 < len(exp) and wrong == exp[pos + 1]:
            return "swap/omit"
        if pos >= 1 and wrong == exp[pos - 1]:
            return "double"
        return "near/extra"

    def run(self, units: list) -> None:
        prev = None
        for i, u in enumerate(units):
            was_div = self.diverged()
            if u.kind == "char":
                if len(u.text) == 1 and not was_div:
                    kind = self.classify(u.text)
                    line_before = self.cur_line()
                    self.put(u.text)
                    if self.diverged():                       # здесь началась опечатка
                        self.typos += 1
                        self.starts.append(self.match)
                        self.kinds[kind] = self.kinds.get(kind, 0) + 1
                        if not u.text.isalpha():
                            self.err(i, f"ошибочный символ не буква: {u.text!r}")
                        if not line_before.strip():
                            self.err(i, "опечатка первым символом строки")
                else:
                    if was_div and (len(u.text) != 1 or not is_word(u.text)):
                        self.err(i, f"во время опечатки набран не символ слова: {u.text!r}")
                    for c in u.text:
                        self.put(c)
            elif u.kind == "back":
                if not self.buf:
                    self.err(i, "Backspace в пустом буфере")
                else:
                    if prev is None or prev.kind not in ("char", "back") or (
                            prev.kind == "char" and (len(prev.text) != 1 or not is_word(prev.text))):
                        self.err(i, f"Backspace после {prev.kind if prev else None} {getattr(prev, 'text', '')!r}")
                    if not was_div:
                        self.overdeletes += 1
                    gone = self.buf.pop()
                    self.match = min(self.match, len(self.buf))
                    if not is_word(gone):
                        self.err(i, f"Backspace стёр не символ слова: {gone!r}")
                    if not self.cur_line().strip():
                        self.err(i, "Backspace дошёл до начала строки/отступа")
                    if not (0.3 <= u.k <= 1.1):
                        self.err(i, f"k у Backspace вне диапазона: {u.k:.2f}")
            else:
                if was_div:
                    self.err(i, f"служебная единица {u.kind} посреди опечатки")
                if u.kind == "newline":
                    self.put("\n")
                elif u.kind == "fast":
                    for c in u.text:
                        self.put(c)
                elif u.kind == "tab":
                    for c in self.tab:
                        self.put(c)
                # cleanup / nav / прочее — текст не меняют
            # src_end: ровно столько, сколько уже набрано верно, и не больше длины образца
            if not (0 <= u.src_end <= len(self.src)):
                self.err(i, f"src_end={u.src_end} вне образца")
            elif self.map[u.src_end] != self.match:
                self.err(i, f"src_end={u.src_end} (→{self.map[u.src_end]}), а верно набрано {self.match}")
            if u.pause < 0 or u.pause > 10:
                self.err(i, f"странная пауза {u.pause}")
            prev = u
        if "".join(self.buf) != self.expected:
            self.err(len(units), "итоговый текст не совпадает с образцом")


def simulate(text: str, s: Settings, seed: int) -> Sim:
    sim = Sim(typer.normalize(text), s)
    sim.run(build(typer.normalize(text), s, seed))
    return sim


# ------------------------------------------------------------------ проверки

def test_correctness() -> None:
    variants = [
        dict(profile=PROFILE_IDE),
        dict(profile=PROFILE_PLAIN, fast_indent=False),
        dict(indent_with_tab=True),
        dict(typos_per_100=10.0, think_pause_s=0.0),
        dict(typos_per_100=0.5, think_pause_s=4.0),
    ]
    for name, text in (("python", PY), ("js", JS), ("prose", PROSE), ("tabs", TABBED)):
        for vi, kw in enumerate(variants):
            s = make_settings(**kw)
            bad = []
            for seed in range(60):
                sim = simulate(text, s, seed)
                if sim.errors:
                    bad.append(f"seed={seed}: " + "; ".join(sim.errors[:2]))
            check(f"корректность: {name}, вариант {vi} ({kw})", not bad, bad[0] if bad else "")
    bad = []
    for t in SHORT:
        for seed in range(200):
            sim = simulate(t, make_settings(typos_per_100=10.0), seed)
            if sim.errors:
                bad.append(f"{t!r} seed={seed}: {sim.errors[0]}")
    check("корректность: короткие тексты", not bad, bad[0] if bad else "")


def test_real_build_units() -> None:
    # обычный путь без подмены генератора — как в приложении
    s = make_settings(typos_per_100=3.0)
    bad = []
    for _ in range(20):
        sim = Sim(PY, s)
        sim.run(typer.build_units(PY, s))
        bad += sim.errors
    check("build_units(human_typing=True) без seed", not bad, bad[0] if bad else "")


def test_rate() -> None:
    for name, text in (("python", PY), ("js", JS), ("prose", PROSE)):
        n = len(typer.normalize(text))
        for rate in (0.5, 1.5, 4.0):
            s = make_settings(typos_per_100=rate)
            seeds = 300
            total = sum(simulate(text, s, seed).typos for seed in range(seeds))
            got = total / seeds / (n / 100)
            check(f"частота: {name}, {rate}/100 → {got:.2f}/100", abs(got - rate) <= 0.08 * rate + 0.02)
    s = make_settings(typos_per_100=0.0)
    zero = all(simulate(PY, s, seed).typos == 0 and not any(u.kind == "back" for u in build(PY, s, seed))
               for seed in range(30))
    check("частота: 0 → опечаток нет", zero)
    s = make_settings(typos_per_100=10.0)
    n = len(PROSE)
    got = sum(simulate(PROSE, s, seed).typos for seed in range(100)) / 100 / (n / 100)
    check(f"частота: максимум 10/100 → {got:.2f}/100", 7.0 <= got <= 10.5)
    # короткий текст: в среднем не больше, чем позволяет частота
    s = make_settings(typos_per_100=1.5)
    t = "print('hi')"
    got = sum(simulate(t, s, seed).typos for seed in range(3000)) / 3000
    check(f"частота: короткий текст {got:.3f} ≤ {1.5 * len(t) / 100:.3f}", got <= 1.5 * len(t) / 100 * 1.15)


def test_gap_and_kinds() -> None:
    s = make_settings(typos_per_100=10.0)
    min_gap = 10 ** 9
    kinds: dict[str, int] = {}
    episodes = over = 0
    for seed in range(150):
        sim = simulate(PROSE + PY, s, seed)
        episodes += sim.typos
        over += sim.overdeletes
        for k, v in sim.kinds.items():
            kinds[k] = kinds.get(k, 0) + v
        for x, y in zip(sim.starts, sim.starts[1:]):
            min_gap = min(min_gap, y - x)
    # якоря опечаток не ближе 8 символов; «удвоение»/«лишняя клавиша» расходятся на символ позже
    check(f"опечатки не ближе 8 символов (минимум {min_gap})", min_gap >= 7)
    total = sum(kinds.values())
    share = {k: round(v / total, 2) for k, v in sorted(kinds.items())}
    check(f"виды опечаток встречаются все: {share}",
          all(kinds.get(k, 0) > 0 for k in ("case", "swap/omit", "double", "near/extra")))
    check(f"лишнее стирание ≈10%: {over / max(1, episodes):.3f}", 0.04 <= over / max(1, episodes) <= 0.16)


def test_rhythm() -> None:
    s = make_settings(typos_per_100=0.0)
    for name, text in (("python", PY), ("js", JS), ("prose", PROSE)):
        ks = [u.k for seed in range(20) for u in build(text, s, seed) if u.kind == "char" and len(u.text) == 1]
        mean = sum(ks) / len(ks)
        # максимум — микропаузы между словами (k ≈ 3–6), внутри слов k < 1
        check(f"средний темп k ≈ 1: {name} ({mean:.3f}, разброс {min(ks):.2f}–{max(ks):.2f})",
              0.95 <= mean <= 1.05 and min(ks) > 0.3 and max(ks) <= 7.0)
    units = build(PY, make_settings(typos_per_100=0.0), 1)
    caps = [u.k for u in units if u.kind == "char" and u.text.isupper()]
    check("заглавные медленнее среднего", sum(caps) / len(caps) > 1.0)
    pauses = [u.pause for u in units if u.pause]
    check(f"паузы обдумывания есть ({len(pauses)}) и разумны (макс {max(pauses):.2f} с)",
          len(pauses) > 5 and max(pauses) < 4)
    nothink = build(PY, make_settings(think_pause_s=0.0, typos_per_100=0.0), 1)
    check("think_pause_s=0 → пауз нет", not any(u.pause for u in nothink))
    s = make_settings(typos_per_100=3.0)
    notice = []
    for seed in range(40):
        us = build(PY, s, seed)
        for a, b in zip(us, us[1:]):
            if b.kind == "back" and a.kind == "char":
                notice.append(b.pause)
    check(f"пауза «заметил» 0.1–0.75 с (мин {min(notice):.2f}, макс {max(notice):.2f})",
          notice and min(notice) >= 0.1 and max(notice) <= 0.75)


def typed_lines(units: list) -> list[list]:
    """Строки набора: подряд идущие единицы char (без отступа); Enter, отступ и прочее их разделяют."""
    out, cur = [], []
    for u in units:
        if u.kind == "char" and len(u.text) == 1:
            if cur or not u.text.isspace():
                cur.append(u)
        else:
            if cur:
                out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def cv(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5 / m


def test_burst() -> None:
    """Ритм очередями: слово быстро и ровно, разброс — в промежутках между словами."""
    s = make_settings(typos_per_100=0.0)
    seeds = 40
    for name, text in (("python", PY), ("js", JS), ("prose", PROSE)):
        inside, gaps, hes_pauses = [], [], []
        runs = 0
        for seed in range(seeds):
            for ln in typed_lines(build(text, s, seed)):
                run = 0             # промежутков подряд без заметной паузы
                for a, b in zip(ln, ln[1:]):
                    if is_word(a.text) and is_word(b.text):
                        inside.append(a.k)                  # внутри слова
                    elif a.text == " " and is_word(b.text):
                        gaps.append(a.k)                    # пробел → слово
                        if b.pause:
                            hes_pauses.append(b.pause)
                        if a.k < 2.5 and not b.pause:
                            run += 1
                        else:
                            runs += run >= 2
                            run = 0
                    elif b.pause:
                        hes_pauses.append(b.pause)          # заминка перед «"слово», «(x», «self.name»…
                runs += run >= 2
        m_in, m_gap = sum(inside) / len(inside), sum(gaps) / len(gaps)
        cv_in, cv_gap = cv(inside), cv(gaps)
        micro = sum(g >= 3 for g in gaps) / len(gaps)
        hes = len(hes_pauses) / len(gaps)
        check(f"очереди: {name}: внутри слова k {m_in:.2f} < 0.85, между словами {m_gap:.2f} > 1.5",
              m_in < 0.85 and m_gap > 1.5)
        check(f"очереди: {name}: внутри слова ровнее (CV {cv_in:.2f} против {cv_gap:.2f})", cv_in < 0.6 * cv_gap)
        check(f"очереди: {name}: микропаузы (k ≥ 3) {micro:.1%} — не редкость и не на каждом шагу",
              0.06 <= micro <= 0.3)
        check(f"очереди: {name}: долгие заминки посреди строки {hes:.1%}, 0.25–0.9 с",
              0.01 <= hes <= 0.08 and min(hes_pauses) >= 0.25 and max(hes_pauses) <= 0.9)
        check(f"очереди: {name}: поток — 3+ слова подряд без пауз ({runs / seeds:.1f} раз на текст)",
              runs / seeds >= 1)
    # без пауз на обдумывание долгих заминок нет, но микропаузы между словами остаются
    s = make_settings(typos_per_100=0.0, think_pause_s=0.0)
    gaps = [a.k for seed in range(20) for ln in typed_lines(build(PROSE, s, seed))
            for a, b in zip(ln, ln[1:]) if a.text == " " and is_word(b.text)]
    micro = sum(g >= 3 for g in gaps) / len(gaps)
    check(f"очереди: think_pause_s=0 — микропаузы остаются ({micro:.1%})", 0.06 <= micro <= 0.35)
    # заданная скорость не врёт: среднее k ≈ 1 при опечатках и разных настройках
    bad = []
    for kw in (dict(typos_per_100=3.0), dict(profile=PROFILE_PLAIN, fast_indent=False),
               dict(indent_with_tab=True, typos_per_100=1.5)):
        for name, text in (("python", PY), ("js", JS), ("prose", PROSE)):
            ks = [u.k for seed in range(10) for u in build(text, make_settings(**kw), seed)
                  if u.kind == "char" and len(u.text) == 1]
            mean = sum(ks) / len(ks)
            if abs(mean - 1) > 0.1:
                bad.append(f"{name} {kw}: {mean:.3f}")
    check("среднее k ≈ 1 ± 0.1 при опечатках и разных отступах", not bad, "; ".join(bad))


def test_passthrough() -> None:
    U = typer.Unit
    s = make_settings(typos_per_100=10.0)
    units = [U("char", c, i + 1) for i, c in enumerate("alpha beta")]
    nav = U("nav", "", 10)
    units.insert(5, nav)
    out = humanize(list(units), s, random.Random(3))
    check("неизвестные единицы (nav) проходят как есть", nav in out and out.count(nav) == 1)


def test_speed() -> None:
    s = make_settings(typos_per_100=3.0)
    text = (PY + JS + PROSE) * 4
    text = text[:10000]
    base = typer.build_units(text, make_settings(human_typing=False))
    t0 = time.perf_counter()
    humanize(base, s, random.Random(1))
    dt = time.perf_counter() - t0
    check(f"скорость: 10 000 символов за {dt * 1000:.0f} мс", dt < 0.2)


def main() -> int:
    test_correctness()
    test_real_build_units()
    test_rate()
    test_gap_and_kinds()
    test_rhythm()
    test_burst()
    test_passthrough()
    test_speed()
    print("ГОТОВО" if not fails else f"ПРОВАЛОВ: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
