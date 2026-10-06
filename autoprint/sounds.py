"""Звук клавиш: короткие щелчки синтезируются при первом запуске в data/sounds/.

Варианты каждой клавиши слегка отличаются по тону, чтобы не было «пулемёта».
"""
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtMultimedia import QSoundEffect

from .storage import DATA_DIR

RATE = 44100
SOUND_DIR = DATA_DIR / "sounds"
STYLES = {"soft": "Мягкий", "mechanical": "Механический", "typewriter": "Печатная машинка"}
KINDS = ("key", "space", "enter")
VARIANTS = 4
_GEN_VERSION = 1


def _synth(style: str, kind: str, seed: int) -> list[float]:
    rnd = random.Random(seed * 1000 + hash((style, kind)) % 997)
    pitch = 1.0 + rnd.uniform(-0.12, 0.12)
    if kind == "space":
        pitch *= 0.8
    elif kind == "enter":
        pitch *= 0.7

    if style == "soft":
        dur, tau_n, tau_b, f_body, cut, n_amp, b_amp = 0.045, 0.004, 0.012, 170, 0.12, 0.5, 0.8
    elif style == "mechanical":
        dur, tau_n, tau_b, f_body, cut, n_amp, b_amp = 0.05, 0.003, 0.008, 320, 0.45, 0.9, 0.5
    else:  # typewriter
        dur, tau_n, tau_b, f_body, cut, n_amp, b_amp = 0.07, 0.006, 0.02, 230, 0.6, 1.0, 0.7
    if kind == "enter":
        dur *= 1.5
        tau_b *= 1.6

    n = int(RATE * dur)
    out = []
    lp = 0.0
    for i in range(n):
        t = i / RATE
        noise = rnd.uniform(-1, 1)
        lp += cut * (noise - lp)  # однополюсный ФНЧ — «приглушённость»
        s = n_amp * lp * math.exp(-t / tau_n)
        s += b_amp * math.sin(2 * math.pi * f_body * pitch * t) * math.exp(-t / tau_b)
        if style == "typewriter" and kind == "enter":
            s += 0.4 * math.sin(2 * math.pi * 1800 * t) * math.exp(-max(0, t - 0.03) / 0.04) * (t > 0.03)
        out.append(s)
    # плавная атака, чтобы не было щелчка от ступеньки
    for i in range(min(40, n)):
        out[i] *= i / 40
    peak = max(abs(x) for x in out) or 1
    return [x / peak * 0.9 for x in out]


def _write_wav(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


def ensure_sounds() -> None:
    marker = SOUND_DIR / f".v{_GEN_VERSION}"
    if marker.exists():
        return
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    for style in STYLES:
        for kind in KINDS:
            for v in range(VARIANTS):
                _write_wav(SOUND_DIR / f"{style}_{kind}_{v}.wav", _synth(style, kind, v))
    marker.touch()


class KeySoundPlayer(QObject):
    """Живёт в GUI-потоке; play() вызывается через сигнал движка печати."""

    POOL = 3  # экземпляров на файл — чтобы быстрые нажатия накладывались

    def __init__(self, style: str = "soft", volume: int = 35, enabled: bool = True) -> None:
        super().__init__()
        ensure_sounds()
        self._effects: dict[str, list[QSoundEffect]] = {}
        self._rr: dict[str, int] = {}
        self.enabled = enabled
        self._volume = volume / 100
        self._style = ""
        self.set_style(style)

    def set_style(self, style: str) -> None:
        if style not in STYLES:
            style = "soft"
        if style == self._style:
            return
        self._style = style
        self._effects.clear()
        for kind in KINDS:
            pool = []
            for v in range(VARIANTS):
                for _ in range(self.POOL):
                    e = QSoundEffect(self)
                    e.setSource(QUrl.fromLocalFile(str(SOUND_DIR / f"{style}_{kind}_{v}.wav")))
                    e.setVolume(self._volume)
                    pool.append(e)
            random.shuffle(pool)
            self._effects[kind] = pool
            self._rr[kind] = 0

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, volume)) / 100
        for pool in self._effects.values():
            for e in pool:
                e.setVolume(self._volume)

    @Slot(str)
    def play(self, kind: str) -> None:
        if not self.enabled or self._volume <= 0:
            return
        pool = self._effects.get(kind) or self._effects.get("key")
        if not pool:
            return
        i = self._rr[kind] = (self._rr.get(kind, 0) + 1) % len(pool)
        pool[i].play()
