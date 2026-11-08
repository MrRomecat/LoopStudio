"""Прогресс и ETA рендера: секунды подэтапов ffmpeg → общий процент."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from core.media.ffmpeg import ProgressCallback


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    """Одно обновление прогресса для UI."""

    stage_name: str
    stage_index: int
    stage_count: int
    fraction: float  # 0..1 общий прогресс
    eta_seconds: float | None  # None = ещё неизвестно
    elapsed_seconds: float = 0.0  # сколько уже идёт рендер


ProgressSink = Callable[[ProgressUpdate], None]


class PipelineProgress:
    """Агрегатор: этапы с весами, у каждого известен объём в секундах медиа.

    ETA считается по общей скорости: elapsed * (1 - f) / f, сглаживание не
    требуется — фракция монотонна, оценка стабилизируется за секунды.
    """

    def __init__(self, sink: ProgressSink | None, stages: list[tuple[str, float]]) -> None:
        """:param stages: (название, вес) — веса нормализуются автоматически."""
        self._sink = sink
        total = sum(weight for _, weight in stages) or 1.0
        self._stages = [(name, weight / total) for name, weight in stages]
        self._done_fraction = 0.0
        self._current = 0
        self._best = 0.0
        self._started = time.monotonic()

    def _emit(self, fraction: float) -> None:
        if self._sink is None:
            return
        # прогресс-бар никогда не идёт назад, даже если подэтапы перекрываются
        self._best = max(self._best, min(max(fraction, 0.0), 1.0))
        fraction = self._best
        elapsed = time.monotonic() - self._started
        eta = elapsed * (1.0 - fraction) / fraction if fraction > 0.01 else None
        name = self._stages[self._current][0]
        self._sink(
            ProgressUpdate(
                stage_name=name,
                stage_index=self._current + 1,
                stage_count=len(self._stages),
                fraction=fraction,
                eta_seconds=eta,
                elapsed_seconds=elapsed,
            )
        )

    def stage_callback(self, index: int, total_units: float) -> ProgressCallback:
        """Колбэк для run_ffmpeg этапа ``index`` объёмом ``total_units`` секунд."""
        self._current = index
        _, weight = self._stages[index]

        def on_progress(units: float) -> None:
            inner = min(units / total_units, 1.0) if total_units > 0 else 1.0
            self._emit(self._done_fraction + weight * inner)

        return on_progress

    def stage_done(self, index: int) -> None:
        """Отметить этап завершённым (и молчаливые этапы тоже двигают полосу)."""
        self._current = index
        self._done_fraction = sum(weight for _, weight in self._stages[: index + 1])
        self._emit(self._done_fraction)
