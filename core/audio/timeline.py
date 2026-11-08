"""Резиновый таймлайн — закон продукта: музыка никогда не обрезается.

Треки добавляются по кругу, пока итоговая длительность микса (с паузами и
кроссфейдами) не достигнет заданной. Последний трек всегда доигрывает целиком,
поэтому итог всегда >= заданной длительности. Видеоряд и эмбиент подгоняются
под итог.

Взаимодействие настроек (утверждённая логика):
- пауза > 0 — треки разделены тишиной (в ней играет только эмбиент);
  кроссфейд в этом случае не применяется — трекам нечего перекрывать;
- пауза == 0 и кроссфейд > 0 — соседние треки перекрываются на длину
  кроссфейда, итоговая длительность уменьшается на кроссфейд за каждый стык.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """Один трек в итоговом миксе: где начинается и сколько длится."""

    path: Path
    start: float
    duration: float


@dataclass(frozen=True, slots=True)
class Timeline:
    """Готовый план мастер-дорожки."""

    entries: tuple[TimelineEntry, ...]
    total_duration: float
    pause: float
    crossfade: float


class TimelineError(ValueError):
    """Невозможно построить таймлайн (нет треков, некорректные параметры)."""


def build_timeline(
    tracks: list[tuple[Path, float]],
    target_duration: float,
    *,
    pause: float = 0.0,
    crossfade: float = 0.0,
) -> Timeline:
    """Построить резиновый таймлайн.

    :param tracks: плейлист (путь, длительность в секундах) в итоговом порядке.
    :param target_duration: заданная длительность ролика, сек.
    :param pause: пауза между треками, сек (музыка молчит, эмбиент играет).
    :param crossfade: кроссфейд между треками, сек (только при pause == 0).
    """
    if not tracks:
        raise TimelineError("Плейлист пуст: в папке нет поддерживаемых аудиофайлов.")
    if target_duration <= 0:
        raise TimelineError("Длительность ролика должна быть больше нуля.")
    if any(duration <= 0 for _, duration in tracks):
        broken = [path.name for path, duration in tracks if duration <= 0]
        raise TimelineError(f"Треки с нулевой длительностью: {', '.join(broken)}")

    effective_crossfade = 0.0 if pause > 0 else crossfade
    if effective_crossfade > 0:
        shortest = min(duration for _, duration in tracks)
        if effective_crossfade >= shortest:
            raise TimelineError(
                f"Кроссфейд ({effective_crossfade:g} с) не короче самого короткого трека "
                f"({shortest:g} с) — уменьшите кроссфейд."
            )

    entries: list[TimelineEntry] = []
    total = 0.0
    index = 0
    while total < target_duration:
        path, duration = tracks[index % len(tracks)]
        start = total + pause - effective_crossfade if entries else 0.0
        entries.append(TimelineEntry(path=path, start=start, duration=duration))
        total = start + duration
        index += 1

    return Timeline(
        entries=tuple(entries), total_duration=total, pause=pause, crossfade=effective_crossfade
    )
