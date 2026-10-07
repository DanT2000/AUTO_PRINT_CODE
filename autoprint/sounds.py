"""Звук клавиш — записи настоящих клавиатур (CC0, см. autoprint/sounds/SOURCES.md).

Из записанных нажатий и отпусканий при первом выборе звука собираются варианты в data/sounds/:
нажатие + отпускание через естественную паузу, чуть разная высота и сила удара — чтобы не было «пулемёта».
Пробел и Enter — крупные клавиши: ниже по тону и громче.
"""
from __future__ import annotations

import random
import struct
import wave
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtMultimedia import QSoundEffect

from .storage import DATA_DIR, LEGACY_SOUND_STYLES as LEGACY_STYLES

RATE = 44100
PACK_DIR = Path(__file__).resolve().parent / "sounds"
SOUND_DIR = DATA_DIR / "sounds"
STYLES = {
    "office": "Обычная офисная клавиатура",
    "brown": "Механическая, тактильная (Cherry MX Brown)",
    "red": "Механическая, линейная",
    "cream": "Механическая, глухая («thock»)",
    "blue": "Механическая, щелчковая (Kailh Box Jade)",
}
DEFAULT_STYLE = "office"
KINDS = ("key", "space", "enter")
VARIANTS = 8
_GEN_VERSION = 3

# kind → (высота тона, громкость, пауза до отпускания, мс)
_KIND = {"key": (1.0, 1.0, (70, 115)), "space": (0.86, 1.12, (85, 135)), "enter": (0.9, 1.25, (95, 150))}


def _read(path: Path) -> list[float]:
    with wave.open(str(path)) as w:
        return [x / 32768 for x in struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))]


def _write(path: Path, s: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", round(max(-1, min(1, x)) * 32767)) for x in s))


def _repitch(s: list[float], factor: float) -> list[float]:
    """Сдвиг тона пересэмплированием (factor < 1 — ниже и чуть длиннее)."""
    if abs(factor - 1) < 1e-3:
        return list(s)
    n = int(len(s) / factor)
    out = []
    for i in range(n):
        x = i * factor
        j = int(x)
        a = s[j] if j < len(s) else 0.0
        b = s[j + 1] if j + 1 < len(s) else 0.0
        out.append(a + (b - a) * (x - j))
    return out


def _mix(downs: list[list[float]], ups: list[list[float]], kind: str, rnd: random.Random) -> list[float]:
    pitch, gain, (gap_lo, gap_hi) = _KIND[kind]
    pitch *= rnd.uniform(0.97, 1.03)
    gain *= rnd.uniform(0.85, 1.0)
    out = _repitch(rnd.choice(downs), pitch)
    if ups:
        up = _repitch(rnd.choice(ups), pitch * rnd.uniform(0.98, 1.02))
        at = int(RATE * rnd.uniform(gap_lo, gap_hi) / 1000)
        out += [0.0] * max(0, at + len(up) - len(out))
        ug = rnd.uniform(0.75, 1.0)
        for i, x in enumerate(up):
            out[at + i] += x * ug
    peak = max(abs(x) for x in out) * gain
    if peak > 0.97:   # громче — да, с перегрузом — нет
        gain *= 0.97 / peak
    return [x * gain for x in out]


def ensure_style(style: str) -> None:
    marker = SOUND_DIR / f".{style}.v{_GEN_VERSION}"
    if marker.exists():
        return
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    for old in SOUND_DIR.glob(f"{style}_*.wav"):
        old.unlink()
    pack = PACK_DIR / style
    downs = [_read(p) for p in sorted(pack.glob("down_*.wav"))]
    ups = [_read(p) for p in sorted(pack.glob("up_*.wav"))]
    rnd = random.Random(style)
    for kind in KINDS:
        for v in range(VARIANTS):
            _write(SOUND_DIR / f"{style}_{kind}_{v}.wav", _mix(downs, ups, kind, rnd))
    marker.touch()


def _cleanup_legacy() -> None:
    """Синтезированные звуки прежних версий больше не нужны."""
    if (SOUND_DIR / ".v1").exists():
        for p in SOUND_DIR.glob("*.wav"):
            if p.name.split("_")[0] in LEGACY_STYLES:
                p.unlink(missing_ok=True)
        (SOUND_DIR / ".v1").unlink(missing_ok=True)


class KeySoundPlayer(QObject):
    """Живёт в GUI-потоке; play() вызывается через сигнал движка печати."""

    POOL = 2  # экземпляров на файл — чтобы быстрые нажатия накладывались

    def __init__(self, style: str = DEFAULT_STYLE, volume: int = 35, enabled: bool = True) -> None:
        super().__init__()
        _cleanup_legacy()
        self._effects: dict[str, list[QSoundEffect]] = {}
        self._rr: dict[str, int] = {}
        self.enabled = enabled
        self._volume = 0.0
        self.set_volume(volume)
        self._style = ""
        self.set_style(style)

    def set_style(self, style: str) -> None:
        style = LEGACY_STYLES.get(style, style)
        if style not in STYLES:
            style = DEFAULT_STYLE
        if style == self._style:
            return
        ensure_style(style)
        self._style = style
        for pool in self._effects.values():
            for e in pool:
                e.deleteLater()
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
