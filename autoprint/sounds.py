"""Звук клавиш — записи настоящих клавиатур (CC0, см. autoprint/sounds/SOURCES.md) и свои записи пользователя.

Из записанных нажатий и отпусканий при первом выборе звука собираются варианты в data/sounds/:
нажатие + отпускание через естественную паузу, чуть разная высота и сила удара — чтобы не было «пулемёта».
Пробел и Enter — крупные клавиши: ниже по тону и громче.

Свои наборы (мастер «Записать звук клавиатуры», см. recorder.py) лежат в data/sounds/custom/<slug>/:
key_NN.wav, space_NN.wav, enter_NN.wav — уже целые звуки (нажатие вместе с отпусканием) и meta.json.
Ключ такого набора — «my_<slug>».

Варианты собираются в фоновом потоке: пересэмплирование на чистом Python занимает секунды,
и первый выбор звука не должен подвешивать окно.
"""
from __future__ import annotations

import json
import logging
import random
import re
import shutil
import sys
import threading
import wave
from array import array
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtMultimedia import QSoundEffect

from .storage import DATA_DIR, LEGACY_SOUND_STYLES as LEGACY_STYLES

log = logging.getLogger("autoprint.sounds")

RATE = 44100
PACK_DIR = Path(__file__).resolve().parent / "sounds"
SOUND_DIR = DATA_DIR / "sounds"
CUSTOM_DIR = SOUND_DIR / "custom"
CUSTOM_PREFIX = "my_"
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

_SLUG = re.compile(r"[A-Za-z0-9_-]+")
# сборка вариантов — по одной за раз: фоновые потоки плеера и удаление набора не должны пересекаться
_lock = threading.Lock()


def read_wav(path: Path) -> list[float]:
    with wave.open(str(path)) as w:
        a = array("h")
        a.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        a.byteswap()
    return [x / 32768 for x in a]


def write_wav(path: Path, s: list[float]) -> None:
    a = array("h", [round(max(-1.0, min(1.0, x)) * 32767) for x in s])
    if sys.byteorder == "big":
        a.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.tobytes())


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


def _limit(s: list[float], gain: float) -> list[float]:
    peak = max((abs(x) for x in s), default=0.0) * gain
    if peak > 0.97:   # громче — да, с перегрузом — нет
        gain *= 0.97 / peak
    return [x * gain for x in s]


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
    return _limit(out, gain)


def custom_variants(groups: dict[str, list[list[float]]], rnd: random.Random,
                    count: int = VARIANTS) -> dict[str, list[list[float]]]:
    """Варианты своего набора. Записи — уже целые звуки, поэтому у вида со своими записями — только
    ±3 % по высоте и чуть разная сила удара. Если пробел или Enter не записаны — берутся обычные клавиши,
    ниже по тону и громче, как во встроенных наборах."""
    keys = [c for c in groups.get("key") or [] if c]
    if not keys:
        raise ValueError("в наборе нет записей обычных клавиш")
    out: dict[str, list[list[float]]] = {}
    for kind in KINDS:
        own = [c for c in groups.get(kind) or [] if c]
        src = own or keys
        pitch, gain, _gap = (1.0, 1.0, None) if own else _KIND[kind]
        order = list(src)
        rnd.shuffle(order)   # по кругу из перемешанных: разные записи, пока их хватает
        out[kind] = [_limit(_repitch(order[v % len(order)], pitch * rnd.uniform(0.97, 1.03)),
                            gain * rnd.uniform(0.85, 1.0)) for v in range(count)]
    return out


# ---------------------------------------------------------------- свои наборы

def is_custom(style: str) -> bool:
    return style.startswith(CUSTOM_PREFIX)


def custom_dir(key: str) -> Path:
    """Папка своего набора: my_<slug> → data/sounds/custom/<slug>."""
    slug = key[len(CUSTOM_PREFIX):] if is_custom(key) else ""
    if not _SLUG.fullmatch(slug):
        raise ValueError(f"не свой набор звуков: {key!r}")
    return CUSTOM_DIR / slug


def custom_styles() -> dict[str, str]:
    """Свои наборы: ключ → название, по порядку записи."""
    try:
        dirs = [d for d in CUSTOM_DIR.iterdir() if d.is_dir() and _SLUG.fullmatch(d.name)]
    except OSError:
        return {}
    found = []
    for d in dirs:
        if not any(d.glob("key_*.wav")):
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        name = " ".join(str(meta.get("name") or "").split()) or d.name
        found.append((str(meta.get("created", "")), name, CUSTOM_PREFIX + d.name))
    found.sort()
    return {key: name for _created, name, key in found}


def available_styles() -> dict[str, str]:
    """Все наборы для выбора: встроенные (STYLES) и свои («Своя: …»)."""
    styles = dict(STYLES)
    styles.update({key: f"Своя: {name}" for key, name in custom_styles().items()})
    return styles


def resolve_style(style: str) -> str:
    """Набор, который реально прозвучит: старые названия → новые, неизвестный или удалённый → по умолчанию."""
    style = LEGACY_STYLES.get(style, style)
    if style in STYLES:
        return style
    if is_custom(style):
        try:
            if any(custom_dir(style).glob("key_*.wav")):
                return style
        except (ValueError, OSError):
            pass
    return DEFAULT_STYLE


def clear_rendered(style: str) -> None:
    """Убрать собранные варианты набора и отметку о сборке (файлы других наборов не трогаются:
    у «my_a» и «my_a_2» общее начало имени)."""
    own = re.compile(re.escape(style) + r"_(?:key|space|enter)_\d+\.wav")
    try:
        files = [p for p in SOUND_DIR.glob(f"{style}_*.wav") if own.fullmatch(p.name)]
        files += list(SOUND_DIR.glob(f".{style}.v*"))
    except OSError:
        return
    for p in files:
        try:
            p.unlink(missing_ok=True)
        except OSError:   # файл ещё держит проигрыватель — перезапишется при следующей сборке
            pass


def delete_custom(key: str) -> None:
    """Удалить свой набор вместе с собранными вариантами. Если он был выбран, set_style() сам
    перейдёт на набор по умолчанию (а настройку стоит поменять: DEFAULT_STYLE)."""
    d = custom_dir(key)
    with _lock:
        clear_rendered(key)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- сборка вариантов

def _marker(style: str) -> Path:
    return SOUND_DIR / f".{style}.v{_GEN_VERSION}"


def variant_path(style: str, kind: str, v: int) -> Path:
    return SOUND_DIR / f"{style}_{kind}_{v}.wav"


def is_rendered(style: str) -> bool:
    return _marker(style).exists() and all(variant_path(style, k, v).exists()
                                           for k in KINDS for v in range(VARIANTS))


def ensure_style(style: str) -> None:
    """Собрать варианты набора в data/sounds/, если их ещё нет. Долго — не из GUI-потока."""
    with _lock:
        marker = _marker(style)
        if is_rendered(style):
            return
        SOUND_DIR.mkdir(parents=True, exist_ok=True)
        clear_rendered(style)
        rnd = random.Random(style)
        if is_custom(style):
            d = custom_dir(style)
            groups = {kind: [read_wav(p) for p in sorted(d.glob(f"{kind}_*.wav"))] for kind in KINDS}
            for kind, clips in custom_variants(groups, rnd).items():
                for v, s in enumerate(clips):
                    write_wav(variant_path(style, kind, v), s)
        else:
            pack = PACK_DIR / style
            downs = [read_wav(p) for p in sorted(pack.glob("down_*.wav"))]
            ups = [read_wav(p) for p in sorted(pack.glob("up_*.wav"))]
            if not downs:
                raise FileNotFoundError(f"нет записей набора «{style}» в {pack}")
            for kind in KINDS:
                for v in range(VARIANTS):
                    write_wav(variant_path(style, kind, v), _mix(downs, ups, kind, rnd))
        marker.touch()


def _cleanup_legacy() -> None:
    """Синтезированные звуки прежних версий больше не нужны."""
    if (SOUND_DIR / ".v1").exists():
        for p in SOUND_DIR.glob("*.wav"):
            if p.name.split("_")[0] in LEGACY_STYLES:
                p.unlink(missing_ok=True)
        (SOUND_DIR / ".v1").unlink(missing_ok=True)


class KeySoundPlayer(QObject):
    """Живёт в GUI-потоке; play() вызывается через сигнал движка печати.

    Если набор ещё не собран, сборка идёт в фоновом потоке, а QSoundEffect создаются уже здесь,
    в GUI-потоке, когда придёт _rendered. Пока набор собирается, play() молчит. Если за это время
    выбрали другой набор — побеждает последний выбор."""

    POOL = 2  # экземпляров на файл — чтобы быстрые нажатия накладывались
    ready = Signal(str)                 # набор загружен и звучит (ключ набора)
    _rendered = Signal(str, int, str)   # из фонового потока: набор, номер запроса, ошибка ("" — нет)

    def __init__(self, style: str = DEFAULT_STYLE, volume: int = 35, enabled: bool = True) -> None:
        super().__init__()
        _cleanup_legacy()
        self._effects: dict[str, list[QSoundEffect]] = {}
        self._rr: dict[str, int] = {}
        self.enabled = enabled
        self._volume = 0.0
        self.set_volume(volume)
        self._style = ""     # загруженный набор
        self._wanted = ""    # выбранный (может ещё собираться)
        self._request = 0
        self._rendered.connect(self._on_rendered, Qt.ConnectionType.QueuedConnection)
        self.set_style(style)

    @property
    def style(self) -> str:
        return self._wanted

    @property
    def loading(self) -> bool:
        return self._wanted != self._style

    def set_style(self, style: str) -> None:
        style = resolve_style(style)
        if style == self._wanted:
            return
        self._wanted = style
        self._request += 1
        if is_rendered(style):
            self._load(style)
            return
        self._unload()   # пока собирается новый — тишина, а не звук прежнего набора
        threading.Thread(target=self._render, args=(style, self._request), name=f"sounds-{style}",
                         daemon=True).start()

    def _render(self, style: str, request: int) -> None:
        """Фоновый поток: только файлы, никаких объектов Qt."""
        err = ""
        try:
            ensure_style(style)
        except Exception as e:  # noqa: BLE001 — ошибка сборки не должна ронять программу
            log.exception("Не удалось собрать звук «%s»", style)
            err = f"{type(e).__name__}: {e}"
        try:
            self._rendered.emit(style, request, err)
        except RuntimeError:   # плеер уже удалён (окно закрыли раньше)
            pass

    @Slot(str, int, str)
    def _on_rendered(self, style: str, request: int, err: str) -> None:
        if request != self._request:
            return
        if err:
            if style != DEFAULT_STYLE:
                self._wanted = ""
                self.set_style(DEFAULT_STYLE)
            return
        self._load(style)

    def _unload(self) -> None:
        for pool in self._effects.values():
            for e in pool:
                e.stop()
                e.deleteLater()
        self._effects.clear()
        self._style = ""

    def _load(self, style: str) -> None:
        self._unload()
        self._style = style
        for kind in KINDS:
            pool = []
            for v in range(VARIANTS):
                url = QUrl.fromLocalFile(str(variant_path(style, kind, v)))
                for _ in range(self.POOL):
                    e = QSoundEffect(self)
                    e.setSource(url)
                    e.setVolume(self._volume)
                    pool.append(e)
            random.shuffle(pool)
            self._effects[kind] = pool
            self._rr[kind] = 0
        self.ready.emit(style)

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
