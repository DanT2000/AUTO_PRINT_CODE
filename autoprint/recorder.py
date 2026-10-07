"""Запись своей клавиатуры микрофоном: захват звука, нарезка на отдельные удары, сохранение набора.

Нарезка, отбор и сохранение — чистый Python без Qt (проверяются тестами на синтетическом сигнале,
numpy не нужен: огибающая считается окнами по 2 мс, 20 секунд записи режутся за доли секунды).
Захват — небольшой класс AudioRecorder поверх QtMultimedia.

Набор сохраняется в data/sounds/custom/<slug>/ (key_NN.wav, space_NN.wav, enter_NN.wav, meta.json),
дальше его подхватывает sounds.py как обычный набор с ключом «my_<slug>».
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import sys
import time
from array import array

from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSource, QMediaDevices, QtAudio

from . import sounds

RATE = sounds.RATE
TARGET_PEAK = 0.9

_CHECK = "проверьте Параметры Windows → Конфиденциальность → Микрофон и что выбран нужный микрофон"
SILENT_MSG = f"Похоже, микрофон молчит: {_CHECK}"
OPEN_MSG = f"Не удалось открыть микрофон: {_CHECK}"


# ---------------------------------------------------------------- нарезка на удары

def _peak(c) -> float:
    return max(max(c), -min(c)) if len(c) else 0.0


def _rms(c) -> float:
    return math.sqrt(sum(x * x for x in c) / len(c)) if len(c) else 0.0


def _envelope(samples, win: int, dc: float) -> list[float]:
    """Пик |x| в окнах по win отсчётов (max/min по срезу — быстро и без numpy)."""
    env = []
    for i in range(0, len(samples), win):
        seg = samples[i:i + win]
        env.append(max(max(seg) - dc, dc - min(seg)))
    return env


def hit_spans(samples, rate: int = RATE, *, pre_ms: float = 5.0, post_ms: float = 220.0,
              refractory_ms: float = 110.0, release_ms: float = 300.0, max_ms: float = 350.0,
              min_level: float = 0.003) -> list[tuple[int, int]]:
    """Где в записи удары клавиш: отрезки [начало, конец) в отсчётах, по времени.

    Огибающая — пик в окнах по 2 мс со скользящим максимумом на 6 мс. Порог — от уровня шума
    (нижняя треть огибающей) и от самого громкого удара. Нажатие и отпускание одной клавиши —
    один удар: новый удар не раньше refractory_ms после предыдущего, а тихий щелчок вскоре
    после удара (до release_ms) считается отпусканием и входит в тот же отрезок."""
    n = len(samples)
    if n == 0:
        return []
    win = max(1, round(rate * 0.002))
    dc = sum(samples) / n
    env = _envelope(samples, win, dc)
    sm = [max(env[max(0, k - 2):k + 1]) for k in range(len(env))]
    srt = sorted(sm)
    noise, peak = srt[int(len(srt) * 0.35)], srt[-1]
    if peak < min_level:
        return []
    thr = max(noise * 3.0, noise + (peak - noise) * 0.08, min_level)
    low = noise + (thr - noise) * 0.4   # гистерезис: новый удар — только после спада
    if peak < thr:
        return []

    def windows(ms: float) -> int:
        return max(1, round(ms / 1000 * rate / win))

    refr, rel, pw = windows(refractory_ms), windows(release_ms), windows(30)
    cands = []
    armed = True
    for k, e in enumerate(sm):
        if armed:
            if e >= thr:
                armed = False
                if not cands or k - cands[-1] >= refr:
                    cands.append(k)
        elif e < low:
            armed = True

    hits: list[list] = []   # [окно начала, пик, окно последнего отпускания]
    for k in cands:
        p = max(env[k:k + pw])
        if hits and k - hits[-1][0] < rel and p < hits[-1][1] * 0.45:
            hits[-1][2] = k   # отпускание той же клавиши
            continue
        hits.append([k, p, None])

    def ms(v: float) -> int:
        return round(v / 1000 * rate)

    tail_floor = max(noise * 2.0, 1e-6)
    onsets = []
    for k, p, _rel in hits:
        j = k   # назад, пока огибающая выше шума: мягкое начало удара
        while j > 0 and k - j < 5 and env[j - 1] > tail_floor:
            j -= 1
        level = max(tail_floor, thr * 0.3)
        lo, hi = j * win, min(n, (k + 1) * win)
        onsets.append(next((i for i in range(lo, hi) if abs(samples[i] - dc) >= level), k * win))

    spans = []
    for i, (k, p, rel_k) in enumerate(hits):
        onset = onsets[i]
        start = max(0, onset - ms(pre_ms))
        end = onset + ms(post_ms)
        if rel_k is not None:
            end = max(end, rel_k * win + ms(60))
        end = min(end, onset + ms(max_ms), n)
        if i + 1 < len(hits):
            end = min(end, max(onset + ms(20), onsets[i + 1] - ms(pre_ms)))
        # хвостовая тишина: до последнего окна, где ещё что-то звучит, и 8 мс запаса
        quiet = max(tail_floor, p * 0.02)
        last = next((w for w in range(min(len(env), -(-end // win)) - 1, onset // win - 1, -1)
                     if env[w] > quiet), onset // win)
        end = min(end, (last + 1) * win + ms(8))
        end = max(end, min(n, onset + ms(20)))
        if end > start:
            spans.append((start, end))
    return spans


def cut_clip(samples, start: int, end: int, rate: int = RATE, dc: float | None = None) -> list[float]:
    """Отрезок записи без постоянной составляющей, с плавным входом (2 мс) и выходом (до 30 мс)."""
    seg = samples[start:end]
    if dc is None:
        dc = sum(samples) / len(samples) if len(samples) else 0.0
    c = [x - dc for x in seg]
    fi = min(len(c), round(rate * 0.002))
    for i in range(fi):
        c[i] *= i / fi
    fo = min(len(c) // 3, round(rate * 0.030))
    for i in range(fo):
        c[-1 - i] *= i / fo
    return c


def slice_hits(samples, rate: int = RATE, **kw) -> list[list[float]]:
    """Отдельные удары клавиш из записи (каждый — от ~5 мс до удара до ~220 мс после), по времени."""
    if not len(samples):
        return []
    dc = sum(samples) / len(samples)
    return [cut_clip(samples, a, b, rate, dc) for a, b in hit_spans(samples, rate, **kw)]


# ---------------------------------------------------------------- отбор и громкость

def is_key_hit(clip, rate: int = RATE) -> bool:
    """Удар клавиши короткий: основная энергия — в первых десятках миллисекунд. Долгий ровный звук
    (голос, гул, шорох) ударом не считается."""
    if len(clip) < rate * 0.12:
        return True
    head = _rms(clip[:round(rate * 0.05)])
    tail = _rms(clip[round(rate * 0.09):])
    return tail < head * 0.6


def filter_hits(clips: list[list[float]], rate: int = RATE) -> list[list[float]]:
    """Отбросить то, что на удар не похоже: намного тише остальных или долгий ровный звук."""
    peaks = [_peak(c) for c in clips]
    good = [p for p in peaks if p > 0]
    if not good:
        return []
    med = statistics.median(good)
    return [c for c, p in zip(clips, peaks) if p > 0 and p >= med * 0.25 and is_key_hit(c, rate)]


def normalize_groups(groups: dict[str, list[list[float]]], target: float = TARGET_PEAK,
                     rate: int = RATE) -> dict[str, list[list[float]]]:
    """Отбор в каждой группе и общее усиление для всех: самый громкий пик ≈ target, а соотношение
    громкости (пробел громче буквы) сохраняется."""
    kept = {k: filter_hits(v, rate) for k, v in groups.items()}
    top = max((_peak(c) for v in kept.values() for c in v), default=0.0)
    if top <= 0:
        return {k: [] for k in groups}
    g = target / top
    return {k: [[x * g for x in c] for c in v] for k, v in kept.items()}


def normalize_set(clips: list[list[float]], target: float = TARGET_PEAK, rate: int = RATE) -> list[list[float]]:
    """Общее усиление (самый громкий пик ≈ target, соотношение ударов сохраняется) и отсев не-ударов."""
    return normalize_groups({"key": clips}, target, rate)["key"]


# ---------------------------------------------------------------- сохранение набора

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ("a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                      "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya")))
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


def _slug(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    slug = re.sub(r"_+", "_", "".join(out)).strip("_")[:32].strip("_") or "keyboard"
    return slug + "_kb" if slug in _RESERVED else slug   # «con», «nul»… Windows не даст так назвать папку


def save_custom_pack(name: str, groups: dict[str, list[list[float]]]) -> str:
    """Сохранить свой набор и вернуть его ключ («my_<slug>», уникальный).

    groups: "key" — обязательно, "space" и "enter" — можно пустыми (тогда при сборке возьмутся
    обычные клавиши, ниже по тону и громче)."""
    if not [c for c in groups.get("key") or [] if c]:
        raise ValueError("нужна хотя бы одна запись обычной клавиши")
    name = " ".join(name.split()) or "Моя клавиатура"
    taken = set(sounds.custom_styles().values())
    base_name, i = name, 2
    while name in taken:   # одинаковые названия в списке звуков не различить
        name = f"{base_name} ({i})"
        i += 1
    base = _slug(base_name)
    sounds.CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    slug, i = base, 2
    while (sounds.CUSTOM_DIR / slug).exists():
        slug = f"{base}_{i}"
        i += 1
    key = sounds.CUSTOM_PREFIX + slug
    final = sounds.custom_dir(key)
    tmp = sounds.CUSTOM_DIR / f".{slug}.tmp"   # с точки — custom_styles() не увидит недописанный набор
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    counts = {}
    try:
        for kind in sounds.KINDS:
            clips = [c for c in groups.get(kind) or [] if c]
            counts[kind] = len(clips)
            for n, c in enumerate(clips):
                sounds.write_wav(tmp / f"{kind}_{n:02d}.wav", c)
        meta = {"name": name, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "clips": counts}
        (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        sounds.clear_rendered(key)   # остатки набора с тем же ключом, удалённого раньше
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return key


# ---------------------------------------------------------------- захват с микрофона

def _device_id(d: QAudioDevice) -> str:
    return bytes(d.id().data()).decode("utf-8", "replace")


_SF = QAudioFormat.SampleFormat
# формат отсчёта → (код array, деление, сдвиг)
_DECODE = {_SF.Int16: ("h", 32768.0, 0), _SF.Int32: ("i", 2147483648.0, 0), _SF.Float: ("f", 1.0, 0),
           _SF.UInt8: ("B", 128.0, 128)}


def decode_pcm(data: bytes, sample_format, channels: int) -> list[float]:
    """Байты PCM → моно −1..1 (каналы усредняются)."""
    code, scale, shift = _DECODE[sample_format]
    a = array(code)
    width = a.itemsize * max(1, channels)
    a.frombytes(data[:len(data) - len(data) % width])
    if sys.byteorder == "big" and a.itemsize > 1:
        a.byteswap()
    if channels <= 1:
        return [(x - shift) / scale for x in a]
    k = scale * channels
    return [(sum(t) - shift * channels) / k for t in zip(*(a[c::channels] for c in range(channels)))]


def resample(s: list[float], src: int, dst: int = RATE) -> list[float]:
    """Линейная интерполяция: для щелчков клавиш (48 → 44,1 кГц) её достаточно."""
    if src == dst or not s:
        return list(s)
    n = int(len(s) * dst / src)
    step = src / dst
    last = len(s) - 1
    out = [0.0] * n
    for i in range(n):
        x = i * step
        j = int(x)
        a = s[j]
        b = s[j + 1] if j < last else a
        out[i] = a + (b - a) * (x - j)
    return out


class AudioRecorder(QObject):
    """Запись с микрофона: моно, 44 100 Гц, отсчёты −1..1.

    Если устройство не умеет 44 100 Гц / моно / 16 бит, пишется в его родном формате и потом
    переводится. Данные забираются таймером (~20 раз в секунду) — это же частота сигнала level."""
    level = Signal(float)   # пик за последние ~50 мс, 0..1 (для индикатора)
    error = Signal(str)

    MAX_SECONDS = 90
    SILENT_PEAK = 0.002     # тише −54 дБ на всей записи — микрофон фактически ничего не слышит

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._io: QIODevice | None = None
        self._format = QAudioFormat()
        self._keep = True
        self._buf = array("f")
        self._rest = b""
        self._frames = 0
        self._peak = 0.0
        self._warned = False
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._pull)

    # ------------------------------------------------------------ устройства
    @staticmethod
    def devices() -> list[tuple[str, str]]:
        """Микрофоны: [(id, название)]."""
        return [(_device_id(d), d.description()) for d in QMediaDevices.audioInputs()]

    @staticmethod
    def default_device() -> str:
        d = QMediaDevices.defaultAudioInput()
        return "" if d.isNull() else _device_id(d)

    @staticmethod
    def _find(device_id: str | None) -> QAudioDevice | None:
        if device_id:
            for d in QMediaDevices.audioInputs():
                if _device_id(d) == device_id:
                    return d
        d = QMediaDevices.defaultAudioInput()
        return None if d.isNull() else d

    # ------------------------------------------------------------ запись
    @property
    def recording(self) -> bool:
        return self._io is not None

    @property
    def seconds(self) -> float:
        rate = self._format.sampleRate()
        return self._frames / rate if rate > 0 else 0.0

    def start(self, device_id: str | None = None, keep: bool = True) -> bool:
        """Начать запись. keep=False — только индикатор уровня, отсчёты не копятся."""
        self._close()
        dev = self._find(device_id)
        if dev is None:
            self.error.emit("Микрофон не найден: подключите его и выберите в списке")
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(_SF.Int16)
        if not dev.isFormatSupported(fmt):
            fmt = dev.preferredFormat()
        if fmt.sampleFormat() not in _DECODE or fmt.sampleRate() <= 0 or fmt.channelCount() <= 0:
            self.error.emit("Этот микрофон отдаёт звук в незнакомом формате — выберите другой")
            return False
        src = QAudioSource(dev, fmt, self)
        src.setBufferSize(fmt.bytesForDuration(500_000))   # полсекунды — таймер успевает забирать
        io = src.start()
        if io is None or src.error() != QtAudio.Error.NoError:
            src.deleteLater()
            self.error.emit(OPEN_MSG)
            return False
        src.stateChanged.connect(self._on_state)
        self._source = src
        self._attach(io, fmt, keep)
        return True

    def _attach(self, io: QIODevice, fmt: QAudioFormat, keep: bool = True) -> None:
        """Забирать звук из io (в тестах сюда подставляется QBuffer)."""
        self._io, self._format, self._keep = io, fmt, keep
        self._buf = array("f")
        self._rest = b""
        self._frames = 0
        self._peak = 0.0
        self._warned = False
        self._t0 = time.monotonic()
        self._timer.start()

    def stop(self) -> list[float]:
        """Остановить и вернуть запись: моно, 44 100 Гц, −1..1 (пусто при keep=False)."""
        if self._io is None:
            return []
        self._pull()
        keep, frames, peak, rate = self._keep, self._frames, self._peak, self._format.sampleRate()
        samples = self._buf.tolist()
        warned = self._warned
        self._close()
        if keep and not warned and frames >= rate * 0.5 and peak < self.SILENT_PEAK:
            self.error.emit(SILENT_MSG)
        return resample(samples, rate, RATE) if keep else []

    def _close(self) -> None:
        self._timer.stop()
        src, self._source, self._io = self._source, None, None
        self._buf = array("f")
        self._rest = b""
        if src is not None:
            try:
                src.stateChanged.disconnect(self._on_state)
            except (RuntimeError, TypeError):
                pass
            src.stop()
            src.deleteLater()

    def _pull(self) -> None:
        io = self._io
        if io is None:
            return
        data = self._rest + bytes(io.readAll().data())
        fmt = self._format
        width = max(1, fmt.bytesPerFrame())
        cut = len(data) - len(data) % width
        data, self._rest = data[:cut], data[cut:]
        chunk = decode_pcm(data, fmt.sampleFormat(), fmt.channelCount()) if data else []
        peak = min(1.0, _peak(chunk)) if chunk else 0.0
        self._frames += len(chunk)
        self._peak = max(self._peak, peak)
        if self._keep and len(self._buf) < self.MAX_SECONDS * fmt.sampleRate():
            self._buf.extend(chunk)
        self.level.emit(peak)
        # цифровая тишина дольше полутора секунд — доступ к микрофону, скорее всего, закрыт
        # (данных может не приходить вовсе — поэтому по часам, а не по числу отсчётов)
        if not self._warned and self._peak < 1e-5 and time.monotonic() - self._t0 > 1.5:
            self._warned = True
            self.error.emit(SILENT_MSG)

    def _on_state(self, state) -> None:
        src = self._source
        if src is None or state != QtAudio.State.StoppedState:
            return
        err = src.error()
        if err == QtAudio.Error.NoError:
            return
        if err == QtAudio.Error.OpenError:
            msg = OPEN_MSG
        elif err == QtAudio.Error.IOError:
            msg = "Микрофон перестал отвечать — он не отключился?"
        else:
            msg = "Запись с микрофона прервалась"
        self.error.emit(msg)
