"""Треклист с таймкодами (txt для описания YouTube) и главы mp4 (ffmetadata)."""

from __future__ import annotations

from pathlib import Path

from core.audio.timeline import Timeline


def _timecode(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _track_title(path: Path) -> str:
    return path.stem


def build_tracklist_text(timeline: Timeline) -> str:
    """Строки «ЧЧ:ММ:СС Название» — авторы вставляют в описание ролика."""
    lines = [f"{_timecode(e.start)} {_track_title(e.path)}" for e in timeline.entries]
    return "\n".join(lines) + "\n"


def build_ffmetadata(timeline: Timeline, *, title: str) -> str:
    """ffmetadata-файл: title + главы по трекам (плееры и YouTube их подхватывают)."""
    out = [";FFMETADATA1", f"title={_escape(title)}", ""]
    for i, entry in enumerate(timeline.entries):
        end = (
            timeline.entries[i + 1].start
            if i + 1 < len(timeline.entries)
            else timeline.total_duration
        )
        out += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={round(entry.start * 1000)}",
            f"END={round(end * 1000)}",
            f"title={_escape(_track_title(entry.path))}",
            "",
        ]
    return "\n".join(out)


def _escape(value: str) -> str:
    """Экранирование спецсимволов ffmetadata (= ; # \\ и перевод строки)."""
    for char in ("\\", "=", ";", "#"):
        value = value.replace(char, f"\\{char}")
    return value.replace("\n", "\\\n")
