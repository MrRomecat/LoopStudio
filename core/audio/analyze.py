"""Анализ трека одним декод-проходом: тишина на краях + громкость (LUFS).

Питает сразу три функции:
- авто-обрезку тишины в хвостах (silencedetect → границы);
- точную двухпроходную нормализацию (loudnorm print_format=json → measured_*);
- линтер громкости плейлиста («трек громче медианы на 6 дБ», клиппинг).

Файл только читается; вызов блокирующий (полный декод) — запускать в фоне.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.media.ffmpeg import find_ffmpeg

_CREATE_NO_WINDOW = 0x08000000

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
#: хвост естественного затухания, который оставляем при обрезке тишины
NATURAL_TAIL_SEC = 0.3


class AnalyzeError(RuntimeError):
    """Не удалось проанализировать трек (понятное сообщение)."""


@dataclass(frozen=True, slots=True)
class LoudnessStats:
    """Замер loudnorm для точной (линейной) нормализации второго прохода."""

    input_i: float  # integrated LUFS
    input_tp: float  # true peak, dBTP
    input_lra: float  # loudness range
    input_thresh: float


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    """Итог анализа одного трека."""

    path: Path
    duration: float
    lead_silence: float  # тишина в начале, сек (уже с учётом естественного зазора)
    tail_silence: float  # тишина в конце, сек
    loudness: LoudnessStats | None

    @property
    def trimmed_bounds(self) -> tuple[float, float]:
        """(start, end) для atrim после обрезки краёв."""
        start = max(0.0, self.lead_silence)
        end = max(start + 0.1, self.duration - self.tail_silence)
        return start, end

    @property
    def trimmed_duration(self) -> float:
        start, end = self.trimmed_bounds
        return end - start


def _parse_silence_edges(
    stderr: str, duration: float, min_keep: float = NATURAL_TAIL_SEC
) -> tuple[float, float]:
    """Тишина на краях файла из вывода silencedetect (середину не трогаем)."""
    starts = [float(m.group(1)) for m in _SILENCE_START.finditer(stderr)]
    ends = [float(m.group(1)) for m in _SILENCE_END.finditer(stderr)]

    lead = 0.0
    if starts and starts[0] <= 0.05:
        first_end = ends[0] if ends else duration
        lead = max(0.0, first_end - 0.1)  # маленький зазор перед вступлением

    tail = 0.0
    if starts:
        last_start = starts[-1]
        # последняя тишина «до конца файла»: у неё либо нет end, либо end у конца
        closes_file = len(ends) < len(starts) or (ends and ends[-1] >= duration - 0.05)
        if closes_file and last_start > lead:
            tail = max(0.0, duration - last_start - min_keep)

    # защита от вырождения: не оставляем меньше 1 секунды звука
    if duration - lead - tail < 1.0:
        return 0.0, 0.0
    return lead, tail


def _parse_loudnorm_json(stderr: str) -> LoudnessStats | None:
    """JSON-блок loudnorm в конце stderr → замеры (None, если блока нет)."""
    brace = stderr.rfind("{")
    if brace == -1:
        return None
    try:
        data = json.loads(stderr[brace : stderr.rfind("}") + 1])
        return LoudnessStats(
            input_i=float(data["input_i"]),
            input_tp=float(data["input_tp"]),
            input_lra=float(data["input_lra"]),
            input_thresh=float(data["input_thresh"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def analyze_track(
    path: Path,
    duration: float,
    *,
    silence_threshold_db: float = -60.0,
    min_silence_sec: float = 0.3,
) -> TrackAnalysis:
    """Полный анализ трека одним декодом: края тишины + замер громкости.

    :param duration: длительность по ffprobe (loudnorm её не сообщает).
    :raises AnalyzeError: файл не декодируется.
    """
    filters = (
        f"silencedetect=n={silence_threshold_db:g}dB:d={min_silence_sec:g},"
        f"loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json"
    )
    cmd = [
        str(find_ffmpeg()),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-af",
        filters,
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,  # оконный exe: без DEVNULL наследуется битый хэндл
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        raise AnalyzeError(
            f"Не удалось проанализировать «{path.name}»: файл повреждён или "
            f"формат не поддерживается."
        )
    lead, tail = _parse_silence_edges(result.stderr, duration)
    return TrackAnalysis(
        path=path,
        duration=duration,
        lead_silence=lead,
        tail_silence=tail,
        loudness=_parse_loudnorm_json(result.stderr),
    )
