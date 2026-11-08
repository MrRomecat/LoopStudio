"""Процедурная генерация встроенной библиотеки ассетов LoopStudio.

Всё создаётся синтезом (numpy + ffmpeg) — никаких сторонних материалов,
поэтому лицензия чистая: свободное использование без ограничений.

Выход (assets/):
- ambient/   — 10 бесшовных эмбиент-лупов, FLAC 48 кГц стерео, ~75 c
- overlays/  — 5 VFX-оверлеев (mp4, светлые частицы на чёрном → screen-blend),
               1280x720, луп 12 c
- demo/      — 2 анимированных видеофона (mp4 1080p, бесшовные ~24 c)
- demo/music — 3 ambient-трека (FLAC, ~66 c)
- LICENSES.txt

Запуск:  .venv\\Scripts\\python scripts\\generate_assets.py  [--force]
Файлы, которые уже существуют, пропускаются (кэш между запусками).

ВАЖНО про детерминированность: эмбиенты делят один генератор случайности,
поэтому воспроизводимый результат даёт только ПОЛНАЯ перегенерация (--force);
точечная перегенерация одного файла даст другой (но равноценный) звук.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# консоль Windows может быть в cp1251 — не падать на символах прогресса
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.media.ffmpeg import find_ffmpeg  # noqa: E402

ASSETS = ROOT / "assets"
SR = 48_000
AUDIO_SECONDS = 75.0
LOOP_XFADE = 2.0  # кроссфейд краёв → математически бесшовный луп

rng = np.random.default_rng(20260718)


# ============================ утилиты аудио ====================================


def _write_wav(path: Path, stereo: np.ndarray) -> None:
    data = np.clip(stereo, -1.0, 1.0)
    pcm = (data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _to_flac(wav_path: Path, flac_path: Path) -> None:
    subprocess.run(
        [
            str(find_ffmpeg()),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav_path),
            "-c:a",
            "flac",
            "-ar",
            str(SR),
            str(flac_path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    wav_path.unlink(missing_ok=True)


def _loopify(x: np.ndarray) -> np.ndarray:
    """Кроссфейд хвоста в начало: y[0] бесшовно продолжает y[-1]."""
    n = int(AUDIO_SECONDS * SR)
    xf = int(LOOP_XFADE * SR)
    body, tail = x[:n], x[n : n + xf]
    ramp = np.linspace(0.0, 1.0, xf)
    body = body.copy()
    body[:xf] = body[:xf] * ramp + tail * (1.0 - ramp)
    return body


def _normalize(x: np.ndarray, peak: float = 0.5) -> np.ndarray:
    m = float(np.max(np.abs(x)) or 1.0)
    return x * (peak / m)


def _noise(n: int) -> np.ndarray:
    return rng.standard_normal(n)


def _pink(n: int) -> np.ndarray:
    """Розовый шум (1/f) через формирование спектра."""
    spec = np.fft.rfft(_noise(n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    freqs[0] = freqs[1]
    spec *= 1.0 / np.sqrt(freqs)
    return np.fft.irfft(spec, n)


def _brown(n: int) -> np.ndarray:
    x = np.cumsum(_noise(n))
    x -= np.linspace(x[0], x[-1], n)  # убрать дрейф → края сходятся
    return x / (np.max(np.abs(x)) or 1.0)


def _bandpass(x: np.ndarray, low: float, high: float) -> np.ndarray:
    """Полосовой фильтр через FFT (точно и без scipy)."""
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    mask = ((freqs >= low) & (freqs <= high)).astype(float)
    # мягкие склоны на октаву в обе стороны
    soft_low = (freqs >= low / 2) & (freqs < low)
    soft_high = (freqs > high) & (freqs <= high * 2)
    mask[soft_low] = np.linspace(0, 1, int(np.sum(soft_low)) or 1)
    mask[soft_high] = np.linspace(1, 0, int(np.sum(soft_high)) or 1)
    return np.fft.irfft(spec * mask, len(x))


def _lfo(n: int, hz: float, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * hz * t + phase)


def _crackles(
    n: int, per_second: float, decay_ms: tuple[float, float], gain: tuple[float, float]
) -> np.ndarray:
    """Поток случайных затухающих щелчков (костёр, винил)."""
    out = np.zeros(n)
    count = int(per_second * n / SR)
    positions = rng.integers(0, n - SR // 10, count)
    for pos in positions:
        length = int(SR * rng.uniform(*decay_ms) / 1000)
        env = np.exp(-np.linspace(0, 6, length))
        out[pos : pos + length] += (
            rng.uniform(*gain) * rng.choice([-1, 1]) * env * rng.standard_normal(length)
        )
    return out


def _stereo(build_channel: object) -> np.ndarray:
    """Два независимых прохода генератора → широкое стерео."""
    assert callable(build_channel)
    left = build_channel()
    right = build_channel()
    return np.stack([left, right], axis=1)


# ============================ рецепты эмбиентов ================================


def _rain(intensity: float) -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)

    def channel() -> np.ndarray:
        base = _bandpass(_pink(n), 300, 9000) * 0.8
        patter = 1.0 + 0.12 * _lfo(n, 0.13, rng.uniform(0, 6)) + 0.08 * _lfo(n, 0.031)
        drops = _crackles(n, 6 * intensity, (2, 7), (0.05, 0.22))
        drops = _bandpass(drops, 1200, 10000)
        return _loopify(base * patter * intensity + drops)

    return _normalize(_stereo(channel), 0.45)


def _ocean() -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)
    # период волн подобран так, чтобы укладываться в луп целым числом
    wave_hz = 3.0 / AUDIO_SECONDS  # 3 волны за луп... медленно и глубоко

    def channel() -> np.ndarray:
        rumble = _bandpass(_brown(n) * 2.0, 30, 500)
        swell = 0.55 + 0.45 * np.sin(2 * np.pi * wave_hz * np.arange(n) / SR) ** 2
        foam = _bandpass(_pink(n), 800, 6000) * np.clip(swell - 0.6, 0, 1) * 1.8
        return _loopify(rumble * swell + foam)

    return _normalize(_stereo(channel), 0.45)


def _wind() -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)

    def channel() -> np.ndarray:
        body = _bandpass(_brown(n) * 2.0, 80, 1100)
        gusts = 0.5 + 0.5 * (
            0.6 * _lfo(n, 0.07, rng.uniform(0, 6)) + 0.4 * _lfo(n, 0.19, rng.uniform(0, 6))
        )
        whistle = _bandpass(_pink(n), 900, 2600) * np.clip(gusts - 0.55, 0, 1) * 0.8
        return _loopify(body * (0.4 + 0.6 * gusts) + whistle)

    return _normalize(_stereo(channel), 0.45)


def _fire() -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)

    def channel() -> np.ndarray:
        roar = _bandpass(_brown(n) * 1.6, 60, 900) * (
            0.75 + 0.18 * _lfo(n, 0.11, rng.uniform(0, 6))
        )
        crackle = _bandpass(_crackles(n, 11, (2, 9), (0.1, 0.5)), 900, 9000)
        pops = _bandpass(_crackles(n, 0.7, (8, 22), (0.4, 0.9)), 300, 4000)
        return _loopify(roar + crackle + pops)

    return _normalize(_stereo(channel), 0.45)


def _stream() -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)

    def channel() -> np.ndarray:
        burble = _bandpass(_pink(n), 500, 5200)
        mod = 1.0 + 0.25 * (
            0.5 * _lfo(n, 3.1, rng.uniform(0, 6))
            + 0.3 * _lfo(n, 5.7, rng.uniform(0, 6))
            + 0.2 * _lfo(n, 7.3, rng.uniform(0, 6))
        )
        splash = _bandpass(_crackles(n, 3.5, (4, 12), (0.06, 0.2)), 1500, 8000)
        return _loopify(burble * mod + splash)

    return _normalize(_stereo(channel), 0.42)


def _crickets() -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)
    t = np.arange(n) / SR

    def channel() -> np.ndarray:
        out = _bandpass(_brown(n), 40, 300) * 0.25  # ночной фон
        for _ in range(5):  # пять сверчков в разных местах
            carrier_hz = rng.uniform(3800, 5200)
            trill_hz = rng.uniform(18, 26)
            cycle = rng.uniform(2.2, 4.0)  # период «песни»
            duty = rng.uniform(0.25, 0.5)
            phase = rng.uniform(0, cycle)
            song = ((t + phase) % cycle) < cycle * duty
            trill = (np.sin(2 * np.pi * trill_hz * t) > 0.2).astype(float)
            tone = np.sin(2 * np.pi * carrier_hz * t)
            out += tone * trill * song * rng.uniform(0.05, 0.1)
        return _loopify(out)

    return _normalize(_stereo(channel), 0.4)


def _vinyl() -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)

    def channel() -> np.ndarray:
        hiss = _bandpass(_pink(n), 1000, 12000) * 0.22
        dust = _bandpass(_crackles(n, 2.2, (1, 5), (0.12, 0.35)), 600, 9000)
        thumps = _bandpass(_crackles(n, 0.25, (15, 30), (0.3, 0.6)), 60, 500)
        return _loopify(hiss + dust + thumps)

    return _normalize(_stereo(channel), 0.35)


def _plain(color: str) -> np.ndarray:
    n = int((AUDIO_SECONDS + LOOP_XFADE) * SR)

    def channel() -> np.ndarray:
        if color == "white":
            return _loopify(_bandpass(_noise(n), 40, 16000))
        if color == "pink":
            return _loopify(_pink(n) * 1.4)
        return _loopify(_bandpass(_brown(n) * 2.2, 25, 2000))

    return _normalize(_stereo(channel), 0.4)


AMBIENTS: list[tuple[str, object]] = [
    ("Дождь", lambda: _rain(0.8)),
    ("Ливень с грозой вдалеке", lambda: _rain(1.25)),
    ("Океан — прибой", _ocean),
    ("Ветер — метель", _wind),
    ("Костёр", _fire),
    ("Лесной ручей", _stream),
    ("Ночные сверчки", _crickets),
    ("Винил — тёплый шум", _vinyl),
    ("Розовый шум — мягкий фон", lambda: _plain("pink")),
    ("Коричневый шум — глубокий сон", lambda: _plain("brown")),
]


# ============================ оверлеи (частицы) ================================

OV_W, OV_H, OV_FPS, OV_SECONDS = 1280, 720, 30, 12.0


def _encode_overlay(path: Path, frames: object) -> None:
    """Частицы (светлые на чёрном) → mp4 h264.

    Оверлей накладывается режимом «screen»: чёрный фон становится
    прозрачным, светлые частицы проступают. Надёжнее alpha-webm и работает
    существующим механизмом наложения LoopStudio. Луп бесшовен по построению
    (траектории частиц периодичны в пределах длительности).
    """
    cmd = [
        str(find_ffmpeg()),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{OV_W}x{OV_H}",
        "-r",
        str(OV_FPS),
        "-i",
        "pipe:0",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    assert callable(frames)
    total = int(OV_SECONDS * OV_FPS)
    for i in range(total):
        proc.stdin.write(frames(i / OV_FPS, OV_SECONDS).tobytes())  # RGB на чёрном
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg не смог закодировать {path.name}")


def _splat(
    canvas: np.ndarray, x: float, y: float, radius: float, color: np.ndarray, strength: float
) -> None:
    """Мягкое гауссово пятно (аддитивно): цвет × сила, БЕЗ двойной альфы.

    Канвас — RGB «светлые частицы на чёрном» (screen-blend в LoopStudio);
    strength 0..1 применяется к цвету один раз.
    """
    r = max(1, int(radius * 3))
    x0, x1 = int(x) - r, int(x) + r + 1
    y0, y1 = int(y) - r, int(y) + r + 1
    if x1 <= 0 or y1 <= 0 or x0 >= OV_W or y0 >= OV_H:
        return
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(OV_W, x1), min(OV_H, y1)
    yy, xx = np.mgrid[cy0:cy1, cx0:cx1]
    d2 = (xx - x) ** 2 + (yy - y) ** 2
    blob = np.exp(-d2 / (2 * radius * radius)) * strength
    for c in range(3):
        canvas[cy0:cy1, cx0:cx1, c] += blob * color[c]


def _line(canvas: np.ndarray, x: float, y: float, length: float, strength: float) -> None:
    """Штрих дождя (2 px, с лёгким наклоном), яркость одинарная."""
    steps = max(2, int(length))
    for s in range(steps):
        yy = int(y + s)
        xx = int(x + s * 0.12)
        fade = max(0.25, 1.0 - abs(s / steps - 0.5) * 1.2)
        value = 235 * strength * fade
        if 0 <= yy < OV_H and 0 <= xx < OV_W:
            canvas[yy, xx, :3] += value
        if 0 <= yy < OV_H and 0 <= xx + 1 < OV_W:
            canvas[yy, xx + 1, :3] += value * 0.6


_WHITE = np.array([255.0, 255.0, 255.0])
_WARM_DUST = np.array([255.0, 246.0, 224.0])
_FIREFLY = np.array([255.0, 196.0, 96.0])


class _Particles:
    """Периодические частицы: за длительность лупа каждая возвращается на место."""

    def __init__(self, count: int, seed: int) -> None:
        r = np.random.default_rng(seed)
        self.x0 = r.uniform(0, OV_W, count)
        self.y0 = r.uniform(0, OV_H, count)
        self.falls = r.integers(1, 4, count)  # целых экранов за луп → бесшовно
        self.drift_amp = r.uniform(5, 40, count)
        self.drift_cycles = r.integers(1, 4, count)  # целых периодов за луп
        self.phase = r.uniform(0, 2 * np.pi, count)
        self.size = r.uniform(0.0, 1.0, count)


def _snow_frames(t: float, total: float) -> np.ndarray:
    p = _snow_frames.particles  # type: ignore[attr-defined]
    canvas = np.zeros((OV_H, OV_W, 3), dtype=np.float32)
    margin = 30
    span = OV_H + 2 * margin
    progress = t / total
    for i in range(len(p.x0)):
        y = (p.y0[i] + progress * p.falls[i] * span) % span - margin
        # синусный дрейф НЕ оборачиваем по ширине: у края частица уходит за
        # кадр и возвращается, вместо телепорта через весь экран
        x = p.x0[i] + p.drift_amp[i] * np.sin(2 * np.pi * p.drift_cycles[i] * progress + p.phase[i])
        radius = 1.2 + 2.6 * p.size[i]
        _splat(canvas, x, y, radius, _WHITE, 0.5 + 0.45 * p.size[i])
    return np.clip(canvas, 0, 255).astype(np.uint8)


_snow_frames.particles = _Particles(220, 1)  # type: ignore[attr-defined]


def _rain_frames(t: float, total: float) -> np.ndarray:
    p = _rain_frames.particles  # type: ignore[attr-defined]
    canvas = np.zeros((OV_H, OV_W, 3), dtype=np.float32)
    margin = 40
    span = OV_H + 2 * margin
    progress = t / total
    for i in range(len(p.x0)):
        falls = 8 + int(p.falls[i]) * 6  # быстрые капли: много экранов за луп
        y = (p.y0[i] + progress * falls * span) % span - margin
        x = (p.x0[i] + progress * p.drift_cycles[i] * OV_W) % OV_W
        _line(canvas, x, y, 12 + 16 * p.size[i], 0.55 + 0.4 * p.size[i])
    return np.clip(canvas, 0, 255).astype(np.uint8)


_rain_frames.particles = _Particles(260, 2)  # type: ignore[attr-defined]


def _dust_frames(t: float, total: float) -> np.ndarray:
    p = _dust_frames.particles  # type: ignore[attr-defined]
    canvas = np.zeros((OV_H, OV_W, 3), dtype=np.float32)
    progress = t / total
    for i in range(len(p.x0)):
        x = p.x0[i] + 60 * np.sin(2 * np.pi * p.drift_cycles[i] * progress + p.phase[i])
        y = p.y0[i] + 40 * np.sin(2 * np.pi * p.falls[i] * progress + p.phase[i] * 1.7)
        twinkle = 0.55 + 0.45 * np.sin(2 * np.pi * (p.drift_cycles[i] + 1) * progress + p.phase[i])
        radius = 1.1 + 2.0 * p.size[i]
        _splat(canvas, x, y, radius, _WARM_DUST, (0.35 + 0.5 * p.size[i]) * twinkle)
    return np.clip(canvas, 0, 255).astype(np.uint8)


_dust_frames.particles = _Particles(110, 3)  # type: ignore[attr-defined]


def _bokeh_frames(t: float, total: float) -> np.ndarray:
    p = _bokeh_frames.particles  # type: ignore[attr-defined]
    canvas = np.zeros((OV_H, OV_W, 3), dtype=np.float32)
    progress = t / total
    for i in range(len(p.x0)):
        x = p.x0[i] + 90 * np.sin(2 * np.pi * p.drift_cycles[i] * progress + p.phase[i])
        y = p.y0[i] + 60 * np.sin(2 * np.pi * p.falls[i] * progress + p.phase[i] * 2.3)
        pulse = 0.6 + 0.4 * np.sin(2 * np.pi * p.falls[i] * progress + p.phase[i])
        radius = 14 + 34 * p.size[i]
        warm = np.array([255.0, 205.0 + 40 * p.size[i], 150.0])
        _splat(canvas, x, y, radius, warm, (0.22 + 0.2 * p.size[i]) * pulse)
    return np.clip(canvas, 0, 255).astype(np.uint8)


_bokeh_frames.particles = _Particles(26, 4)  # type: ignore[attr-defined]


def _fireflies_frames(t: float, total: float) -> np.ndarray:
    p = _fireflies_frames.particles  # type: ignore[attr-defined]
    canvas = np.zeros((OV_H, OV_W, 3), dtype=np.float32)
    progress = t / total
    for i in range(len(p.x0)):
        # блуждание по Лиссажу: целые частоты → на стыке лупа скачка нет
        # (прежний линейный дрейф вверх на нецелое число экранов давал прыжок)
        x = p.x0[i] + 55 * np.sin(2 * np.pi * p.drift_cycles[i] * progress + p.phase[i])
        y = p.y0[i] + 45 * np.sin(2 * np.pi * p.falls[i] * progress + p.phase[i] * 1.9)
        blink = max(0.0, np.sin(2 * np.pi * (2 + p.drift_cycles[i]) * progress + p.phase[i]))
        radius = 1.4 + 2.2 * p.size[i]
        _splat(canvas, x, y, radius, _FIREFLY, 0.95 * blink**3)
    return np.clip(canvas, 0, 255).astype(np.uint8)


_fireflies_frames.particles = _Particles(70, 5)  # type: ignore[attr-defined]


OVERLAYS: list[tuple[str, object]] = [
    ("Снегопад", _snow_frames),
    ("Дождь на объективе", _rain_frames),
    ("Пылинки в луче", _dust_frames),
    ("Боке — тёплые огни", _bokeh_frames),
    ("Светлячки — искры", _fireflies_frames),
]


# ============================ демо-фоны =========================================

BG_W, BG_H, BG_FPS, BG_SECONDS = 960, 540, 25, 24.0


def _encode_background(path: Path, frames: object) -> None:
    cmd = [
        str(find_ffmpeg()),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{BG_W}x{BG_H}",
        "-r",
        str(BG_FPS),
        "-i",
        "pipe:0",
        "-vf",
        "scale=1920:1080:flags=lanczos",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    assert callable(frames)
    total = int(BG_SECONDS * BG_FPS)
    for i in range(total):
        proc.stdin.write(frames(i / (BG_SECONDS * BG_FPS)).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg не смог закодировать {path.name}")


_YY, _XX = np.mgrid[0:BG_H, 0:BG_W].astype(np.float32)
_NX, _NY = _XX / BG_W, _YY / BG_H


def _mist_frame(progress: float) -> np.ndarray:
    """«Туманные волны»: дрейф больших синусоидных полей (период = луп)."""
    ph = 2 * np.pi * progress
    field = (
        np.sin(2 * np.pi * (1.3 * _NX + 0.7 * _NY) + ph)
        + np.sin(2 * np.pi * (0.9 * _NX - 1.1 * _NY) - ph)
        + np.sin(2 * np.pi * (2.1 * _NX + 1.7 * _NY) + 2 * ph + 1.7)
        + np.sin(2 * np.pi * (0.5 * _NY) - 2 * ph)
    ) / 4.0
    depth = 0.5 + 0.5 * field
    r = 12 + 34 * depth**2
    g = 16 + 44 * depth**1.6
    b = 42 + 96 * depth
    vign = 1.0 - 0.35 * ((_NX - 0.5) ** 2 + (_NY - 0.5) ** 2) * 4
    frame = np.stack([r * vign, g * vign, b * vign], axis=2)
    return np.clip(frame, 0, 255).astype(np.uint8)


class _Stars:
    def __init__(self) -> None:
        r = np.random.default_rng(77)
        self.n = 320
        self.x = r.uniform(0, BG_W, self.n).astype(np.float32)
        self.y = r.uniform(0, BG_H, self.n).astype(np.float32)
        self.mag = r.uniform(0.25, 1.0, self.n).astype(np.float32)
        self.tw_hz = r.integers(1, 4, self.n)
        self.tw_ph = r.uniform(0, 2 * np.pi, self.n)


_STARS = _Stars()


def _stars_frame(progress: float) -> np.ndarray:
    ph = 2 * np.pi * progress
    # ночное небо с градиентом и двумя «туманностями», дышащими за период
    base_b = 26 + 26 * _NY
    neb1 = 26 * np.exp(-(((_NX - 0.68) ** 2 + (_NY - 0.3) ** 2) / 0.06)) * (0.7 + 0.3 * np.sin(ph))
    neb2 = 20 * np.exp(-(((_NX - 0.25) ** 2 + (_NY - 0.62) ** 2) / 0.1)) * (0.7 + 0.3 * np.cos(ph))
    frame = np.stack(
        [6 + neb1 * 0.9 + neb2 * 0.4, 8 + neb1 * 0.5 + neb2 * 0.7, base_b + neb1 + neb2], axis=2
    ).astype(np.float32)
    # звёзды: круговой параллакс-дрейф (за луп — полный оборот) + мерцание
    dx = 6 * np.sin(ph)
    dy = 4 * np.cos(ph)
    for i in range(_STARS.n):
        x = (_STARS.x[i] + dx * _STARS.mag[i]) % BG_W
        y = (_STARS.y[i] + dy * _STARS.mag[i]) % BG_H
        tw = 0.65 + 0.35 * np.sin(_STARS.tw_hz[i] * ph + _STARS.tw_ph[i])
        val = 200 * _STARS.mag[i] * tw
        xi, yi = int(x), int(y)
        if 1 <= xi < BG_W - 1 and 1 <= yi < BG_H - 1:
            frame[yi, xi] += val
            frame[yi - 1 : yi + 2, xi - 1 : xi + 2] += val * 0.22
    return np.clip(frame, 0, 255).astype(np.uint8)


BACKGROUNDS: list[tuple[str, object]] = [
    ("Фон — Туманные волны", _mist_frame),
    ("Фон — Звёздное небо", _stars_frame),
]


# ============================ демо-музыка =======================================

TRACK_SECONDS = 66.0
_PENTA = [0, 3, 5, 7, 10, 12]  # минорная пентатоника


def _tone(freq: float, n: int, *, kind: str = "sine") -> np.ndarray:
    t = np.arange(n) / SR
    if kind == "triangle":
        return 2 / np.pi * np.arcsin(np.sin(2 * np.pi * freq * t))
    return np.sin(2 * np.pi * freq * t)


def _demo_track(root_hz: float, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    n = int(TRACK_SECONDS * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    # тёплый дрон-аккорд: тоника, квинта, октава, децима — с расстройкой-хором
    for ratio, gain in [(1.0, 0.30), (1.5, 0.18), (2.0, 0.16), (2.4, 0.08)]:
        for detune in (-0.15, 0.0, 0.15):
            hz = root_hz * ratio * (1 + detune / 100)
            wobble = 1.0 + 0.12 * np.sin(2 * np.pi * r.uniform(0.05, 0.11) * t + r.uniform(0, 6))
            out += _tone(hz, n, kind="triangle") * gain / 3 * wobble
    # мягкая пентатоническая «мелодия»: нота каждые ~3 c с медленной атакой
    note_len = int(3.0 * SR)
    for start in range(0, n - note_len, note_len):
        semitone = int(r.choice(_PENTA))
        hz = root_hz * 2 * (2 ** (semitone / 12))
        env = np.sin(np.linspace(0, np.pi, note_len)) ** 2
        out[start : start + note_len] += _tone(hz, note_len) * env * 0.12
    # общий вдох-выдох + фейды краёв (треки в ролике сшивает сам LoopStudio)
    out *= 0.85 + 0.15 * np.sin(2 * np.pi * t / TRACK_SECONDS * 2 - np.pi / 2)
    edge = int(1.2 * SR)
    out[:edge] *= np.linspace(0, 1, edge)
    out[-edge:] *= np.linspace(1, 0, edge)
    left = out
    right = np.roll(out, int(0.011 * SR))  # лёгкая ширина (Haas)
    return _normalize(np.stack([left, right], axis=1), 0.5)


TRACKS: list[tuple[str, float, int]] = [
    ("01 Демо — Спокойный вечер (Am)", 110.0, 11),
    ("02 Демо — Тихая ночь (Fm)", 87.31, 12),
    ("03 Демо — Рассвет (Cm)", 130.81, 13),
]


LICENSES_TEXT = """LoopStudio — встроенная библиотека ассетов
==========================================

Все файлы в папках ambient/, overlays/ и demo/ СГЕНЕРИРОВАНЫ ПРОЦЕДУРНО
(математический синтез шума, частиц и тонов) специально для LoopStudio.
Они не содержат чужих записей, сэмплов или материалов.

Лицензия: свободное использование без ограничений (эквивалент CC0).
Можно использовать в любых роликах, включая монетизированные каналы
YouTube, без указания авторства. Claim'ы Content ID на эти файлы
невозможны по построению — у них нет правообладателя-третьей стороны.

Демо-треки в demo/music — простые процедурные ambient-композиции для
проверки конвейера. Для настоящих роликов используйте свою музыку.

All files in ambient/, overlays/ and demo/ are PROCEDURALLY GENERATED
for LoopStudio. License: free to use without restrictions (CC0
equivalent), including monetized videos. No attribution required.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Генерация встроенных ассетов LoopStudio")
    parser.add_argument("--force", action="store_true", help="перегенерировать существующие")
    args = parser.parse_args()

    ambient_dir = ASSETS / "ambient"
    overlay_dir = ASSETS / "overlays"
    demo_dir = ASSETS / "demo"
    music_dir = demo_dir / "music"
    for folder in (ambient_dir, overlay_dir, demo_dir, music_dir):
        folder.mkdir(parents=True, exist_ok=True)
        # мусор прерванных прошлых запусков не должен попадать в библиотеку
        for stale in folder.glob("*.tmp.wav"):
            stale.unlink(missing_ok=True)

    tmp_root = Path(tempfile.gettempdir())

    def _publish(tmp: Path, target: Path) -> None:
        """Файл появляется в assets только ЦЕЛИКОМ: прерванная генерация
        не оставляет обрывок, который кэш следующего запуска счёл бы готовым."""
        shutil.move(str(tmp), target)

    for name, builder in AMBIENTS:
        flac = ambient_dir / f"{name}.flac"
        if flac.exists() and not args.force:
            print(f"= {flac.name} (есть)")
            continue
        print(f"♪ {name}…")
        assert callable(builder)
        wav = tmp_root / f"loopstudio_gen_{name}.wav"
        tmp_flac = tmp_root / f"loopstudio_gen_{name}.flac"
        try:
            _write_wav(wav, builder())
            _to_flac(wav, tmp_flac)
            _publish(tmp_flac, flac)
        finally:
            wav.unlink(missing_ok=True)
            tmp_flac.unlink(missing_ok=True)

    for name, frame_fn in OVERLAYS:
        mp4 = overlay_dir / f"{name}.mp4"
        (overlay_dir / f"{name}.webm").unlink(missing_ok=True)  # чистим старый alpha-webm
        if mp4.exists() and not args.force:
            print(f"= {mp4.name} (есть)")
            continue
        print(f"✦ {name}…")
        tmp_mp4 = tmp_root / f"loopstudio_gen_{name}.mp4"
        try:
            _encode_overlay(tmp_mp4, frame_fn)
            _publish(tmp_mp4, mp4)
        finally:
            tmp_mp4.unlink(missing_ok=True)

    for name, frame_fn in BACKGROUNDS:
        mp4 = demo_dir / f"{name}.mp4"
        if mp4.exists() and not args.force:
            print(f"= {mp4.name} (есть)")
            continue
        print(f"▣ {name}…")
        tmp_mp4 = tmp_root / f"loopstudio_gen_{name}.mp4"
        try:
            _encode_background(tmp_mp4, frame_fn)
            _publish(tmp_mp4, mp4)
        finally:
            tmp_mp4.unlink(missing_ok=True)

    for name, root_hz, seed in TRACKS:
        flac = music_dir / f"{name}.flac"
        if flac.exists() and not args.force:
            print(f"= {flac.name} (есть)")
            continue
        print(f"♫ {name}…")
        wav = tmp_root / f"loopstudio_gen_{name}.wav"
        tmp_flac = tmp_root / f"loopstudio_gen_{name}.flac"
        try:
            _write_wav(wav, _demo_track(root_hz, seed))
            _to_flac(wav, tmp_flac)
            _publish(tmp_flac, flac)
        finally:
            wav.unlink(missing_ok=True)
            tmp_flac.unlink(missing_ok=True)

    (ASSETS / "LICENSES.txt").write_text(LICENSES_TEXT, encoding="utf-8")
    print("Готово: assets/ambient, assets/overlays, assets/demo, LICENSES.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
