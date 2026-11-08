"""Анализ медиафайлов через ffprobe (JSON) → :class:`MediaInfo`."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.media.ffmpeg import _CREATE_NO_WINDOW, find_ffprobe


class ProbeError(RuntimeError):
    """Файл не удалось проанализировать (битый, неподдерживаемый, отсутствует)."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Сводка по медиафайлу: то, что нужно движку для таймлайна и рендера."""

    path: Path
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None
    artist: str | None = None  # ID3/тег исполнителя (для кредитов)
    title_tag: str | None = None  # ID3/тег названия трека


def _parse_fps(raw: str | None) -> float | None:
    if not raw:
        return None
    num, _, den = raw.partition("/")
    try:
        n = float(num)
        d = float(den) if den else 1.0
    except ValueError:
        return None
    if d == 0:
        return None
    return n / d if n > 0 else None


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") == codec_type:
            return stream
    return None


@dataclass(frozen=True, slots=True)
class Chapter:
    """Глава файла (трек в длинном ролике)."""

    start: float
    end: float
    title: str


def probe_chapters(path: Path) -> list[Chapter]:
    """Прочитать главы файла (сейчас используется только тестами пайплайна)."""
    cmd = [str(find_ffprobe()), "-v", "error", "-print_format", "json", "-show_chapters", str(path)]
    creationflags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    if result.returncode != 0:
        raise ProbeError(f"Не удалось прочитать главы «{path.name}».")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:  # битый вывод не должен ронять вызвавшего
        raise ProbeError(f"Не удалось разобрать главы «{path.name}».") from exc
    chapters: list[Chapter] = []
    for raw in data.get("chapters", []):
        try:
            start = float(raw.get("start_time", 0.0))
            end = float(raw.get("end_time", 0.0))
        except (TypeError, ValueError):
            continue  # глава с нечисловым временем пропускается, не роняет
        chapters.append(
            Chapter(start=start, end=end, title=str(raw.get("tags", {}).get("title", "")))
        )
    return chapters


def probe(path: Path) -> MediaInfo:
    """Проанализировать файл. Бросает :class:`ProbeError` с понятным сообщением."""
    if not path.is_file():
        raise ProbeError(f"Файл не найден: {path}")
    cmd = [
        str(find_ffprobe()),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    creationflags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    if result.returncode != 0:
        raise ProbeError(
            f"Не удалось проанализировать «{path.name}»: файл повреждён или формат "
            f"не поддерживается.\n{result.stderr.strip()}"
        )
    try:
        data: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe вернул некорректный ответ для «{path.name}».") from exc

    streams: list[dict[str, Any]] = data.get("streams", [])
    fmt: dict[str, Any] = data.get("format", {})
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    if video is None and audio is None:
        raise ProbeError(f"В файле «{path.name}» нет ни видео-, ни аудиопотока.")

    duration_raw = fmt.get("duration") or (video or audio or {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else 0.0
    except ValueError:
        duration = 0.0
    if duration <= 0:
        raise ProbeError(f"Не удалось определить длительность «{path.name}».")

    bit_rate_raw = fmt.get("bit_rate")
    try:
        bit_rate = int(bit_rate_raw) if bit_rate_raw is not None else None
    except ValueError:
        bit_rate = None

    sample_rate_raw = (audio or {}).get("sample_rate")
    try:
        sample_rate = int(sample_rate_raw) if sample_rate_raw is not None else None
    except ValueError:
        sample_rate = None

    # теги контейнера: ffprobe отдаёт ключи в произвольном регистре (Artist/ARTIST)
    tags_raw = fmt.get("tags") or {}
    tags = {str(k).lower(): str(v) for k, v in tags_raw.items()} if tags_raw else {}

    def _tag(name: str) -> str | None:
        value = tags.get(name, "").strip()
        return value or None

    return MediaInfo(
        path=path,
        duration=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video["width"]) if video and "width" in video else None,
        height=int(video["height"]) if video and "height" in video else None,
        fps=_parse_fps((video or {}).get("avg_frame_rate")),
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        sample_rate=sample_rate,
        bit_rate=bit_rate,
        artist=_tag("artist") or _tag("album_artist"),
        title_tag=_tag("title"),
    )
