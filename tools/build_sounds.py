"""Сборка звуков клавиш из записей настоящих клавиатур (CC0) → autoprint/sounds/<набор>/.

Нужен ffmpeg (для MP3). Источники и лицензии — в autoprint/sounds/SOURCES.md.

    python tools/build_sounds.py <папка Single Keys из Keyboard Soundpack #1> <папка с blue/brown/red/cream .mp3+.json>

На выходе для каждого набора: down_NN.wav — нажатие (у офисной клавиатуры вместе с отпусканием),
up_NN.wav — отпускание. Громкость наборов выравнивается, чтобы смена звука не меняла громкость.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import wave
from pathlib import Path

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "autoprint" / "sounds"
TARGET_RMS = 0.16     # громкость атаки (первые 25 мс) после выравнивания
PEAK_LIMIT = 0.95
OFFICE_MAX = 14


def read_wav(path: Path) -> list[float]:
    with wave.open(str(path)) as w:
        assert w.getsampwidth() == 2 and w.getframerate() == RATE, path
        ch = w.getnchannels()
        raw = struct.unpack(f"<{w.getnframes() * ch}h", w.readframes(w.getnframes()))
    return [sum(raw[i:i + ch]) / ch / 32768 for i in range(0, len(raw), ch)]


def write_wav(path: Path, s: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", round(max(-1, min(1, x)) * 32767)) for x in s))


def decode_mp3(path: Path) -> list[float]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return [x / 32768 for x in struct.unpack(f"<{len(raw) // 2}h", raw)]


def envelope(s: list[float], win: int = RATE // 500) -> list[float]:
    return [max(abs(x) for x in s[i:i + win]) for i in range(0, max(1, len(s) - win), win)]


def trim(s: list[float], pre_ms: float = 1.5, tail_db: float = -46) -> list[float]:
    """Обрезка тишины: начало — чуть до атаки, конец — когда звук стих; плавные края."""
    pk = max(abs(x) for x in s) or 1
    start = next(i for i, x in enumerate(s) if abs(x) > pk * 0.08)
    start = max(0, start - int(RATE * pre_ms / 1000))
    thr = pk * 10 ** (tail_db / 20)
    end = len(s) - next(i for i, x in enumerate(reversed(s)) if abs(x) > thr)
    out = list(s[start:min(len(s), end + int(RATE * 0.008))])
    fade_in, fade_out = int(RATE * 0.0006), min(len(out) // 3, int(RATE * 0.012))
    for i in range(fade_in):
        out[i] *= i / fade_in
    for i in range(fade_out):
        out[-1 - i] *= i / fade_out
    return out


def attack_rms(s: list[float]) -> float:
    a = s[:int(RATE * 0.025)]
    return (sum(x * x for x in a) / len(a)) ** 0.5


def second_hit_ratio(s: list[float]) -> float:
    """Насколько громок второй удар относительно первого (отсев «двойных» записей)."""
    env = envelope(s)
    i0 = max(range(len(env)), key=env.__getitem__)
    gap = 30 * 500 // 1000  # 30 мс в окнах по 2 мс
    rest = [e for i, e in enumerate(env) if abs(i - i0) > gap]
    return (max(rest) / env[i0]) if rest else 0


def normalize(downs: list[list[float]], ups: list[list[float]]) -> tuple[list, list]:
    """Одна поправка на весь набор: нажатия — к общей громкости, отпускания — в той же пропорции."""
    rms = sorted(attack_rms(d) for d in downs)[len(downs) // 2]
    g = TARGET_RMS / rms
    g = min(g, PEAK_LIMIT / max(max(abs(x) for x in d) for d in downs))
    return [[x * g for x in d] for d in downs], [[x * g for x in u] for u in ups]


def save(name: str, downs: list, ups: list) -> None:
    folder = OUT / name
    for p in folder.glob("*.wav"):
        p.unlink()
    downs, ups = normalize(downs, ups)
    for i, d in enumerate(downs):
        write_wav(folder / f"down_{i:02d}.wav", d)
    for i, u in enumerate(ups):
        write_wav(folder / f"up_{i:02d}.wav", u)
    print(f"{name}: {len(downs)} нажатий, {len(ups)} отпусканий")


def build_office(src: Path) -> None:
    clips = [(second_hit_ratio(s), s) for s in (read_wav(p) for p in sorted(src.glob("*.wav")))]
    clean = [s for r, s in sorted(clips, key=lambda c: c[0]) if r < 0.45][:OFFICE_MAX]
    save("office", [trim(s, tail_db=-50) for s in clean], [])


def build_clacky(src: Path, name: str) -> None:
    sprite = decode_mp3(src / f"{name}.mp3")
    meta = json.loads((src / f"{name}.json").read_text(encoding="utf-8"))

    def cut(start_ms: float, dur_ms: float) -> list[float]:
        a = int(start_ms / 1000 * RATE)
        return trim(sprite[a:a + int(dur_ms / 1000 * RATE)])
    save(name, [cut(*d) for d in meta["down"]], [cut(*u) for u in meta["up"]])


if __name__ == "__main__":
    build_office(Path(sys.argv[1]))
    for n in ("blue", "brown", "red", "cream"):
        build_clacky(Path(sys.argv[2]), n)
