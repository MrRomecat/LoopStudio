"""Тестовые медиа-фикстуры: генерируются ffmpeg'ом и кэшируются в tests/.fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.media.ffmpeg import find_ffmpeg

FIXTURES_DIR = Path(__file__).parent / ".fixtures"


def _generate(args: list[str], output: Path) -> Path:
    """Создать файл ffmpeg'ом, если он ещё не создан (кэш между запусками)."""
    if output.is_file() and output.stat().st_size > 0:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(find_ffmpeg()), "-y", "-hide_banner", "-loglevel", "error", *args, str(output)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Не удалось создать фикстуру {output.name}:\n{result.stderr}")
    return output


@pytest.fixture(scope="session")
def video_5s() -> Path:
    """5-секундное видео 640x360 30fps с аудиодорожкой (синус 220 Гц)."""
    return _generate(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=5:size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=5",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ],
        FIXTURES_DIR / "video_5s.mp4",
    )


@pytest.fixture(scope="session")
def video_with_metadata() -> Path:
    """Как video_5s, но с метаданными источника — их не должно быть в выходе."""
    return _generate(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=5:size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=5",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-metadata",
            "title=SecretSource",
            "-metadata",
            "comment=SecretComment",
            "-metadata",
            "artist=SecretArtist",
        ],
        FIXTURES_DIR / "video_meta.mp4",
    )


@pytest.fixture(scope="session")
def video_short_audio() -> Path:
    """Видео 5 с, но аудио-дорожка 4.2 с (частый случай реальных mp4)."""
    return _generate(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=5:size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=4.2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
        ],
        FIXTURES_DIR / "video_short_audio.mp4",
    )


@pytest.fixture(scope="session")
def music_dir() -> Path:
    """Папка с тремя короткими wav-треками: 3, 4 и 5 секунд."""
    specs = [("01.wav", 440, 3.0), ("02.wav", 554, 4.0), ("03.wav", 659, 5.0)]
    for name, freq, duration in specs:
        _generate(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={freq}:duration={duration}",
                "-c:a",
                "pcm_s16le",
            ],
            FIXTURES_DIR / "music" / name,
        )
    return FIXTURES_DIR / "music"
