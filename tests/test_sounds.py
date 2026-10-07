"""Проверка звуков клавиш и записи своей клавиатуры (autoprint/sounds.py, autoprint/recorder.py) без микрофона.

    python tests/test_sounds.py

Нарезка на удары проверяется на синтетическом сигнале: шум + щелчки (затухающие всплески),
в том числе пары «нажатие + отпускание». Затем — свой набор: сохранение, список наборов, сборка
вариантов, удаление. И фоновая сборка в KeySoundPlayer: set_style() не ждёт сборки, ready приходит
в GUI-поток. Рабочая папка data/ не используется.
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
import time
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(errors="replace")    # консоль cp1251 не знает «→», «≈» и т.п.
except (AttributeError, ValueError):
    pass
os.environ["AUTOPRINT_DATA"] = tempfile.mkdtemp(prefix="autoprint-sounds-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from autoprint import recorder, sounds  # noqa: E402
from autoprint.recorder import (RATE, hit_spans, normalize_groups, normalize_set, save_custom_pack,  # noqa: E402
                                slice_hits)

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK   " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- синтетический сигнал

def noise(rnd: random.Random, seconds: float, level: float = 0.002) -> list[float]:
    return [rnd.gauss(0, level) for _ in range(int(RATE * seconds))]


def click(rnd: random.Random, amp: float, ms: float = 40, tau_ms: float = 6) -> list[float]:
    """Затухающий всплеск шума — как удар клавиши."""
    n, tau = int(RATE * ms / 1000), RATE * tau_ms / 1000
    s = [rnd.uniform(-1, 1) * math.exp(-i / tau) for i in range(n)]
    pk = max(abs(x) for x in s)
    return [x * amp / pk for x in s]


def add(buf: list[float], at: int, s: list[float]) -> None:
    for i, x in enumerate(s):
        if 0 <= at + i < len(buf):
            buf[at + i] += x


def typing(rnd: random.Random, n: int, *, gap_ms=(380, 520), release_ms: float | None = 80,
           release_amp: float = 0.4, amps=(0.35, 0.6), lead_ms: float = 400, tail_ms: float = 500,
           base=None) -> tuple[list[float], list[int]]:
    """n ударов с паузами; у каждого — отпускание через release_ms (None — без отпускания)."""
    onsets, t = [], int(RATE * lead_ms / 1000)
    for _ in range(n):
        onsets.append(t)
        t += int(RATE * rnd.uniform(*gap_ms) / 1000)
    total = onsets[-1] + int(RATE * tail_ms / 1000)
    buf = base(total) if base else noise(rnd, total / RATE)
    for at in onsets:
        amp = rnd.uniform(*amps)
        add(buf, at, click(rnd, amp))
        if release_ms is not None:
            add(buf, at + int(RATE * release_ms / 1000), click(rnd, amp * release_amp, 25, 4))
    return buf, onsets


def onsets_match(spans, onsets, tol_ms: float = 12) -> bool:
    tol = RATE * tol_ms / 1000
    pre = RATE * 0.005
    return len(spans) == len(onsets) and all(abs((a + pre) - o) <= tol for (a, _b), o in zip(spans, onsets))


def test_slicing() -> None:
    rnd = random.Random(1)
    buf, onsets = typing(rnd, 12)
    spans = hit_spans(buf)
    check("нажатие + отпускание через 80 мс — один удар", len(spans) == 12, f"найдено {len(spans)} из 12")
    check("начало удара найдено точно (±12 мс)", onsets_match(spans, onsets))
    clips = slice_hits(buf)
    lens = [len(c) / RATE * 1000 for c in clips]
    check("длина отрезков 20…360 мс", all(20 <= x <= 360 for x in lens), f"{min(lens):.0f}…{max(lens):.0f} мс")
    check("отпускание через 80 мс вошло в отрезок", all(x >= 100 for x in lens), f"мин. {min(lens):.0f} мс")
    check("плавные края (0 в начале и в конце)", all(abs(c[0]) < 1e-9 and abs(c[-1]) < 1e-9 for c in clips))
    dc = [abs(sum(c) / len(c)) for c in clips]
    check("без постоянной составляющей", max(dc) < 0.01, f"{max(dc):.4f}")

    buf, onsets = typing(random.Random(2), 10, release_ms=150, release_amp=0.3)
    spans = hit_spans(buf)
    check("тихое отпускание через 150 мс — не отдельный удар", len(spans) == 10, f"найдено {len(spans)}")

    buf, onsets = typing(random.Random(3), 15, amps=(0.12, 0.7), release_ms=None)
    check("удары разной силы (0.12…0.7)", len(hit_spans(buf)) == 15, f"найдено {len(hit_spans(buf))}")

    # удар в самом начале и в самом конце записи
    rnd = random.Random(4)
    buf, onsets = typing(rnd, 6, lead_ms=0, tail_ms=30, release_ms=None)
    spans = hit_spans(buf)
    check("удар в начале и в конце буфера", len(spans) == 6 and spans[0][0] == 0 and spans[-1][1] <= len(buf),
          f"найдено {len(spans)}")
    clips = slice_hits(buf)
    check("последний отрезок не пустой", len(clips) == 6 and len(clips[-1]) > RATE * 0.02)

    check("тишина — ни одного удара", slice_hits([0.0] * RATE * 3) == [])
    check("пустая запись", slice_hits([]) == [])
    check("только шум — ни одного удара", len(hit_spans(noise(random.Random(5), 5, 0.004))) == 0)
    hum = [0.1 * math.sin(2 * math.pi * 50 * i / RATE) + x
           for i, x in enumerate(noise(random.Random(6), 5, 0.002))]
    check("ровный гул 50 Гц — ни одного удара", len(hit_spans(hum)) == 0, f"найдено {len(hit_spans(hum))}")
    hum_base = (lambda n: [0.05 * math.sin(2 * math.pi * 50 * i / RATE) + random.gauss(0, 0.002)
                           for i in range(n)])
    buf, onsets = typing(random.Random(7), 9, base=hum_base)
    check("удары на фоне гула", len(hit_spans(buf)) == 9, f"найдено {len(hit_spans(buf))}")

    # скорость: 20 секунд записи
    buf, onsets = typing(random.Random(8), 40, gap_ms=(450, 550))
    t = time.perf_counter()
    clips = slice_hits(array("f", buf))
    dt = time.perf_counter() - t
    check("20 с записи режутся быстрее 1 с", dt < 1.0 and len(clips) == 40,
          f"{dt * 1000:.0f} мс, {len(buf) / RATE:.1f} с, ударов {len(clips)}")


def test_normalize() -> None:
    rnd = random.Random(10)
    clips = [click(rnd, a) + [0.0] * 4000 for a in (0.2, 0.3, 0.4, 0.25)]
    quiet = click(rnd, 0.01) + [0.0] * 4000
    tone = [0.3 * math.sin(2 * math.pi * 300 * i / RATE) for i in range(int(RATE * 0.3))]
    out = normalize_set(clips + [quiet, tone])
    peaks = [max(abs(x) for x in c) for c in out]
    check("отбор: тихий щелчок и долгий тон отброшены", len(out) == 4, f"осталось {len(out)}")
    check("громкий пик ≈ 0.9", abs(max(peaks) - 0.9) < 1e-6, f"{max(peaks):.3f}")
    check("соотношение громкости сохранено", abs(peaks[0] / peaks[2] - 0.5) < 1e-6, f"{peaks[0] / peaks[2]:.3f}")
    g = normalize_groups({"key": clips, "space": [click(rnd, 0.8)], "enter": []})
    top = max(max(abs(x) for x in c) for v in g.values() for c in v)
    check("общее усиление для групп", abs(top - 0.9) < 1e-6 and g["enter"] == [] and len(g["key"]) == 4)


def test_custom_pack() -> list[str]:
    rnd = random.Random(20)
    buf, _ = typing(rnd, 10)
    keys = normalize_set(slice_hits(buf))
    buf, _ = typing(rnd, 4, amps=(0.6, 0.8))
    enter = normalize_set(slice_hits(buf))
    k1 = save_custom_pack("Моя клавиатура", {"key": keys, "space": [], "enter": enter})
    k2 = save_custom_pack("  Моя   клавиатура ", {"key": keys[:3]})
    styles = sounds.available_styles()
    check("ключ своего набора", k1 == "my_moya_klaviatura" and k2 == "my_moya_klaviatura_2", f"{k1}, {k2}")
    check("свой набор в списке", styles.get(k1) == "Своя: Моя клавиатура"
          and styles.get(k2) == "Своя: Моя клавиатура (2)", str({k: v for k, v in styles.items() if k.startswith("my_")}))
    check("встроенные наборы на месте", all(k in styles for k in sounds.STYLES) and list(styles)[:5] == list(sounds.STYLES))
    d = sounds.custom_dir(k1)
    files = sorted(p.name for p in d.iterdir())
    check("файлы набора", "meta.json" in files and "key_00.wav" in files and "enter_00.wav" in files
          and not any(f.startswith("space_") for f in files), ", ".join(files[:6]) + "…")
    for k in (k1, k2):
        sounds.ensure_style(k)
    check("варианты собраны", sounds.is_rendered(k1) and sounds.is_rendered(k2))
    space = sounds.read_wav(sounds.variant_path(k1, "space", 0))
    check("пробел без записи — из обычных клавиш, ниже по тону (длиннее)",
          len(space) > min(len(c) for c in keys), f"{len(space)} отсчётов")
    for bad in ("office", "my_../x", "my_", "x"):
        try:
            sounds.custom_dir(bad)
            check(f"custom_dir({bad!r}) отклонён", False)
        except ValueError:
            pass
    try:
        save_custom_pack("Пусто", {"key": [], "space": keys})
        check("набор без обычных клавиш не сохраняется", False)
    except ValueError:
        check("набор без обычных клавиш не сохраняется", True)
    return [k1, k2]


def wait(app, cond, timeout: float = 30.0) -> bool:
    t = time.monotonic()
    while not cond() and time.monotonic() - t < timeout:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_player(keys: list[str]) -> None:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QThread
    from PySide6.QtMultimedia import QAudioFormat
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    k1, k2 = keys

    got: list[tuple[str, bool]] = []
    t = time.perf_counter()
    p = sounds.KeySoundPlayer("office", 30, True)
    dt = time.perf_counter() - t
    p.ready.connect(lambda s: got.append((s, QThread.currentThread() == app.thread())))
    check("плеер создаётся без ожидания сборки", dt < 0.1, f"{dt * 1000:.0f} мс")
    check("пока набор собирается — тишина", p.loading and not p._effects)
    p.play("key")   # не должно падать
    check("набор по умолчанию собран и загружен", wait(app, lambda: ("office", True) in got), str(got))

    got.clear()
    t = time.perf_counter()
    p.set_style("blue")
    p.set_style("cream")
    dt = time.perf_counter() - t
    check("set_style() на несобранном наборе — сразу", dt < 0.1, f"{dt * 1000:.0f} мс")
    ok = wait(app, lambda: ("cream", True) in got)
    wait(app, lambda: sounds.is_rendered("blue"))
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)
    check("побеждает последний выбор", ok and [s for s, _ in got] == ["cream"] and p.style == "cream"
          and not p.loading, str(got))
    check("ready приходит в GUI-поток", all(main for _s, main in got))

    got.clear()
    p.set_style(k2)
    check("свой набор загружается", wait(app, lambda: (k2, True) in got) and len(p._effects["space"]) > 0)
    got.clear()
    p.set_style("office")    # уже собран — синхронно
    check("собранный набор — без фона", got == [("office", True)], str(got))
    p.set_style("нет такого")
    check("неизвестный набор → по умолчанию", p.style == sounds.DEFAULT_STYLE)
    p.set_style("mechanical")
    check("старое название набора → новое", p.style == "brown")

    # удаление: файлы другого набора с общим началом имени («my_moya_klaviatura_2») остаются
    sounds.delete_custom(k1)
    check("удалённый набор пропал из списка", k1 not in sounds.available_styles()
          and not sounds.custom_dir(k1).exists())
    left = list(sounds.SOUND_DIR.glob(f"{k1}_key_*.wav")) + list(sounds.SOUND_DIR.glob(f".{k1}.v*"))
    check("собранные варианты удалённого набора убраны", not left, ", ".join(x.name for x in left))
    check("соседний набор не задет", sounds.is_rendered(k2) and k2 in sounds.available_styles())
    check("удалённый набор → по умолчанию", sounds.resolve_style(k1) == sounds.DEFAULT_STYLE)
    sounds.delete_custom(k2)    # загружен в плеере — не должно падать
    check("удаление загруженного набора", k2 not in sounds.available_styles())

    # запись: устройства и разбор потока (вместо микрофона — QBuffer: стерео float 48 кГц)
    try:
        devs = recorder.AudioRecorder.devices()
        check("список микрофонов", isinstance(devs, list), f"{len(devs)} шт.")
    except Exception as e:  # noqa: BLE001
        check("список микрофонов", False, repr(e))
    secs, src_rate = 1.0, 48000
    frames = array("f")
    for i in range(int(src_rate * secs)):
        v = 0.5 * math.sin(2 * math.pi * 440 * i / src_rate)
        frames.extend((v, v))
    data = QByteArray(frames.tobytes())
    buf = QBuffer()
    buf.setData(data)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    fmt = QAudioFormat()
    fmt.setSampleRate(src_rate)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    rec = recorder.AudioRecorder()
    levels: list[float] = []
    rec.level.connect(levels.append)
    rec._attach(buf, fmt)
    wait(app, lambda: bool(levels), 2)
    out = rec.stop()
    pk = max(abs(x) for x in out) if out else 0
    check("запись: 48 кГц стерео → 44,1 кГц моно", abs(len(out) - RATE * secs) < 5 and abs(pk - 0.5) < 0.01,
          f"{len(out)} отсчётов, пик {pk:.3f}")
    check("запись: уровень для индикатора", bool(levels) and abs(max(levels) - 0.5) < 0.01)
    errors: list[str] = []
    rec.error.connect(errors.append)
    silent = QBuffer()
    silent.setData(QByteArray(bytes(RATE * 2 * 2)))   # 2 с нулей, Int16 моно
    silent.open(QIODevice.OpenModeFlag.ReadOnly)
    fmt16 = QAudioFormat()
    fmt16.setSampleRate(RATE)
    fmt16.setChannelCount(1)
    fmt16.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    rec._attach(silent, fmt16)
    app.processEvents()
    rec.stop()
    check("запись: молчащий микрофон — понятное сообщение", errors == [recorder.SILENT_MSG], str(errors))
    p.deleteLater()
    app.processEvents()


def main() -> int:
    test_slicing()
    test_normalize()
    keys = test_custom_pack()
    test_player(keys)
    print()
    print("FAIL:" if FAILS else "Все проверки прошли.", ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
