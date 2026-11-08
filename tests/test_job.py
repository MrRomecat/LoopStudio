"""Этап 4: RenderJob — сериализация .lsproj, обратная совместимость, валидация."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.render.job import AmbientConfig, EffectsConfig, JobError, RenderJob


def _valid_job(video: Path, music: Path, out: Path) -> RenderJob:
    return RenderJob(
        video_path=str(video), music_dir=str(music), output_dir=str(out), duration_sec=120.0
    )


def test_roundtrip(tmp_path: Path) -> None:
    job = RenderJob(
        video_path="C:/видео/фон.mp4",
        track_order=["03.wav", "01.wav"],
        ambient=[AmbientConfig(enabled=True, path="rain.wav", volume=0.35)],
        effects=EffectsConfig(ken_burns=True, lut="warm", vignette=True),
        resolution="2160p",
        quality="source",
        duration_sec=7200.0,
    )
    file = tmp_path / "проект.lsproj"
    job.save(file)
    loaded = RenderJob.load(file)
    assert loaded == job


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    """Проект из будущей версии не роняет загрузку."""
    file = tmp_path / "future.lsproj"
    file.write_text(
        '{"schema_version": 99, "duration_sec": 90, "новое_поле": true}', encoding="utf-8"
    )
    job = RenderJob.load(file)
    assert job.duration_sec == 90
    from core.render.job import SCHEMA_VERSION

    assert job.schema_version == SCHEMA_VERSION  # версия файла заменяется текущей


def test_broken_file(tmp_path: Path) -> None:
    file = tmp_path / "битый.lsproj"
    file.write_text("{оборванный json", encoding="utf-8")
    with pytest.raises(JobError, match="Не удалось открыть"):
        RenderJob.load(file)


class TestValidate:
    def test_valid(self, video_5s: Path, music_dir: Path, tmp_path: Path) -> None:
        _valid_job(video_5s, music_dir, tmp_path).validate()

    def test_no_video(self, music_dir: Path, tmp_path: Path) -> None:
        job = RenderJob(music_dir=str(music_dir), output_dir=str(tmp_path), duration_sec=120)
        with pytest.raises(JobError, match="шаг 1"):
            job.validate()

    def test_duration_out_of_range(self, video_5s: Path, music_dir: Path, tmp_path: Path) -> None:
        job = _valid_job(video_5s, music_dir, tmp_path)
        job.duration_sec = 30.0
        with pytest.raises(JobError, match="шаг 6"):
            job.validate()

    def test_ambient_without_file(self, video_5s: Path, music_dir: Path, tmp_path: Path) -> None:
        job = _valid_job(video_5s, music_dir, tmp_path)
        job.ambient = [AmbientConfig(enabled=True, path="")]
        with pytest.raises(JobError, match="шаг 3"):
            job.validate()
