"""Волна 4: встроенные ассеты реально работают в конвейере."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.assets import list_ambients, list_overlays
from core.media.probe import probe
from core.render.job import EffectsConfig, RenderJob
from core.render.pipeline import render_job


def test_builtin_ambient_is_seamless_and_valid() -> None:
    """Эмбиент-луп: 48 кГц стерео, читается probe, длительность как задумано."""
    ambients = list_ambients()
    assert ambients, "встроенные эмбиенты не сгенерированы"
    info = probe(ambients[0].path)
    assert info.has_audio and not info.has_video
    assert info.sample_rate == 48_000
    assert info.duration == pytest.approx(75.0, abs=0.5)


def test_builtin_overlays_are_visible(tmp_path: Path) -> None:
    """Регресс тусклости: у каждого оверлея частицы реально видны в кадре."""
    from core.media.ffmpeg import run_ffmpeg

    for overlay in list_overlays():
        raw = tmp_path / f"{overlay.name}.gray"
        run_ffmpeg(
            [
                "-i",
                str(overlay.path),
                "-ss",
                "5",
                "-frames:v",
                "1",
                "-vf",
                "scale=320:180",
                "-pix_fmt",
                "gray",
                "-f",
                "rawvideo",
                str(raw),
            ]
        )
        data = raw.read_bytes()
        mean = sum(data) / len(data)
        peak = max(data)
        assert peak > 60, f"{overlay.name}: частицы слишком тусклые (пик {peak})"
        assert mean > 0.05, f"{overlay.name}: кадр практически пуст (среднее {mean:.3f})"


def test_builtin_overlay_renders_via_screen_blend(
    video_5s: Path, music_dir: Path, tmp_path: Path
) -> None:
    """Встроенный оверлей (mp4, светлые частицы) накладывается screen-blend'ом."""
    overlays = list_overlays()
    assert overlays, "встроенные оверлеи не сгенерированы"
    snow = next((o for o in overlays if "нег" in o.name.lower()), overlays[0])
    job = RenderJob(
        video_path=str(video_5s),
        music_dir=str(music_dir),
        output_dir=str(tmp_path / "out"),
        duration_sec=60.0,
        resolution="720p",
        quality="standard",
        package_folder=False,
        effects=EffectsConfig(
            seamless_loop=True,
            overlay_path=str(snow.path),
            overlay_opacity=0.7,
            overlay_screen_blend=True,  # mp4 → screen (тёмный фон прозрачен)
        ),
    )
    result = render_job(job)
    assert result.output_path.is_file()
    out = probe(result.output_path)
    assert out.has_video and out.has_audio
    assert out.duration == pytest.approx(60.0, abs=1.0)
