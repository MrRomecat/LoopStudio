"""Автодетект аппаратного кодирования видео (NVENC → QSV → AMF, иначе x264).

Кодер считается доступным, только если пробное кодирование реально прошло:
наличие строки в ``ffmpeg -encoders`` не гарантирует поддержку железом.
Результат кэшируется на время работы приложения.
"""

from __future__ import annotations

import subprocess
import sys

from core.media.ffmpeg import FFmpegError, find_ffmpeg, run_ffmpeg

_HW_CANDIDATES = ("h264_nvenc", "h264_qsv", "h264_amf")
_cache: str | None = None
_cache_ready = False


def _listed_encoders() -> str:
    result = subprocess.run(
        [str(find_ffmpeg()), "-hide_banner", "-encoders"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    return result.stdout if result.returncode == 0 else ""


def _smoke_test(codec: str) -> bool:
    """Реально закодировать 8 кадров: отсеивает кодеры без поддержки железом."""
    try:
        run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=gray:size=256x256:rate=30:duration=0.5",
                "-frames:v",
                "8",
                "-c:v",
                codec,
                "-f",
                "null",
                "-",
            ]
        )
        return True
    except FFmpegError:
        return False


def detect_hw_encoder() -> str | None:
    """Лучший рабочий аппаратный кодер h264 или None (fallback: libx264)."""
    global _cache, _cache_ready
    if _cache_ready:
        return _cache
    listed = _listed_encoders()
    for codec in _HW_CANDIDATES:
        if codec in listed and _smoke_test(codec):
            _cache = codec
            break
    else:
        _cache = None
    _cache_ready = True
    return _cache
