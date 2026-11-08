"""Построители vf-цепочек визуальных эффектов луп-сегмента.

Все эффекты применяются к УЖЕ собранному лупу (после xfade-перехода),
поэтому переход тоже обрабатывается и остаётся бесшовным. Сегмент кодируется
один раз — цена эффектов не зависит от длительности итогового ролика.
"""

from __future__ import annotations

LUT_PRESETS: dict[str, str] = {
    # тёплый: чуть ниже цветовая температура + лёгкий подъём насыщенности
    "warm": "colortemperature=temperature=5200,eq=saturation=1.08",
    # холодный: выше температура, чуть приглушить насыщенность
    "cold": "colortemperature=temperature=8500,eq=saturation=0.95",
    # ночной: темнее, синее, мягче
    "night": "eq=brightness=-0.08:saturation=0.75,colorbalance=bs=0.12:ms=0.06",
    # VHS: шум + смаз + пониженная чёткость
    "vhs": "noise=alls=9:allf=t,eq=saturation=1.15:contrast=1.05,unsharp=5:5:-0.6",
    # сепия: классическая матрица
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
}


def scale_pad(width: int, height: int, fps: float) -> str:
    """Привести кадр к целевому размеру с сохранением пропорций (паддинг чёрным)."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps:g},format=yuv420p"
    )


def ken_burns(
    width: int,
    height: int,
    duration: float,
    fps: float,
    zoom: float = 1.08,
    breath_period: float = 40.0,
) -> str:
    """«Дыхание камеры»: плавный зум до ``zoom`` и обратно циклами ~40 секунд.

    Число циклов подбирается целым на длительность сегмента, поэтому в конце
    сегмента зум гарантированно возвращается к 1.0 — луп остаётся бесшовным
    (старая версия зумила 5 % за весь сегмент: незаметно глазу и со скачком
    кадра на стыке лупа).
    """
    cycles = max(1, round(duration / breath_period))
    frames_per_cycle = max(1.0, duration * fps / cycles)
    amplitude = zoom - 1.0
    omega = 6.283185307 / frames_per_cycle
    return (
        f"zoompan=z='1+{amplitude:.4f}*(0.5-0.5*cos({omega:.8f}*on))':d=1:fps={fps:g}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}"
    )


def lut(name: str) -> str:
    """Цветовая стилизация по имени пресета."""
    if name not in LUT_PRESETS:
        known = ", ".join(sorted(LUT_PRESETS))
        raise ValueError(f"Неизвестный фильтр «{name}». Доступны: {known}")
    return LUT_PRESETS[name]


def vignette(strength: float = 0.3) -> str:
    """Мягкое виньетирование; strength 0..1 → угол PI/6..PI/3."""
    clamped = min(max(strength, 0.0), 1.0)
    angle = 0.5236 + clamped * 0.5236  # PI/6 + s * PI/6
    return f"vignette=angle={angle:.4f}"


def film_grain(strength: int = 8) -> str:
    """Лёгкое киношное зерно (0–30)."""
    clamped = min(max(strength, 0), 30)
    return f"noise=alls={clamped}:allf=t+u"


def compose(*parts: str | None) -> str | None:
    """Собрать непустые фильтры в одну vf-цепочку."""
    chain = [p for p in parts if p]
    return ",".join(chain) if chain else None
