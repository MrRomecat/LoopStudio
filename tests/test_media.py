"""Этап 1: probe возвращает корректные данные, run_ffmpeg работает с прогрессом и отменой."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core.media.ffmpeg import FFmpegCancelled, FFmpegError, run_ffmpeg
from core.media.probe import ProbeError, probe


class TestProbe:
    def test_video_fixture(self, video_5s: Path) -> None:
        info = probe(video_5s)
        assert info.has_video
        assert info.has_audio
        assert info.duration == pytest.approx(5.0, abs=0.2)
        assert info.width == 640
        assert info.height == 360
        assert info.fps == pytest.approx(30.0, abs=0.1)
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"

    def test_wav_fixture(self, music_dir: Path) -> None:
        info = probe(music_dir / "01.wav")
        assert info.has_audio
        assert not info.has_video
        assert info.duration == pytest.approx(3.0, abs=0.05)
        assert info.audio_codec == "pcm_s16le"
        assert info.sample_rate == 44100

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ProbeError, match="не найден"):
            probe(tmp_path / "нет_такого.mp4")

    def test_broken_file(self, tmp_path: Path) -> None:
        broken = tmp_path / "битый.mp4"
        broken.write_bytes(b"\x00" * 256)
        with pytest.raises(ProbeError):
            probe(broken)


class TestRunFFmpeg:
    def test_transcode_with_progress(self, music_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.flac"
        seen: list[float] = []
        run_ffmpeg(["-i", str(music_dir / "03.wav"), str(out)], on_progress=seen.append)
        assert out.is_file()
        assert probe(out).duration == pytest.approx(5.0, abs=0.1)
        assert seen, "прогресс не приходил"
        assert seen == sorted(seen), "прогресс должен быть неубывающим"
        assert seen[-1] == pytest.approx(5.0, abs=0.5)

    def test_error_has_readable_message(self, tmp_path: Path) -> None:
        with pytest.raises(FFmpegError) as exc_info:
            run_ffmpeg(["-i", str(tmp_path / "нет.wav"), str(tmp_path / "out.flac")])
        assert "ffmpeg завершился с ошибкой" in str(exc_info.value)
        assert exc_info.value.stderr_tail

    def test_cancel(self, tmp_path: Path) -> None:
        cancel = threading.Event()
        cancel.set()  # отменяем сразу — процесс должен умереть на первом же чтении
        started = time.monotonic()
        with pytest.raises(FFmpegCancelled):
            run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=duration=600:size=1280x720:rate=30",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryslow",
                    str(tmp_path / "long.mp4"),
                ],
                cancel=cancel,
            )
        assert time.monotonic() - started < 30, "отмена должна убивать процесс быстро"
