"""Этап 0: скелет живой — бинарники находятся, фикстуры генерируются."""

from __future__ import annotations

from pathlib import Path

from core.media.ffmpeg import find_ffmpeg, find_ffprobe


def test_ffmpeg_binaries_found() -> None:
    assert find_ffmpeg().is_file()
    assert find_ffprobe().is_file()


def test_video_fixture_generated(video_5s: Path) -> None:
    assert video_5s.is_file()
    assert video_5s.stat().st_size > 10_000


def test_music_fixtures_generated(music_dir: Path) -> None:
    tracks = sorted(music_dir.glob("*.wav"))
    assert len(tracks) == 3
    assert all(t.stat().st_size > 10_000 for t in tracks)
