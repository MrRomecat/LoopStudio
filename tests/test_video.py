"""Этап 3 (критерий): 60-сек луп из 5-сек фикстуры, точная длина, concat без перекодирования."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.media.probe import probe
from core.video.effects import compose, film_grain, ken_burns, lut, vignette
from core.video.looper import LooperError, OverlaySpec, VideoEncodeSettings, build_looped_video

FAST = VideoEncodeSettings(width=640, height=360, fps=30.0, preset="veryfast", crf=23)


def test_60s_loop_exact_and_stream_copied(video_5s: Path, tmp_path: Path) -> None:
    out = tmp_path / "loop60.mp4"
    build_looped_video(video_5s, 60.0, out, FAST, work_dir=tmp_path, crossfade=1.0)

    info = probe(out)
    assert info.duration == pytest.approx(60.0, abs=0.1), "длительность должна быть точной"
    assert info.has_video and not info.has_audio
    assert (info.width, info.height) == (640, 360)

    # concat не перекодирует: битрейт выхода == битрейту сегмента
    segment = probe(tmp_path / "segment.mp4")
    assert info.bit_rate is not None and segment.bit_rate is not None
    assert info.bit_rate == pytest.approx(segment.bit_rate, rel=0.15)


def test_non_seamless_loop(video_5s: Path, tmp_path: Path) -> None:
    out = tmp_path / "hard.mp4"
    build_looped_video(video_5s, 20.0, out, FAST, work_dir=tmp_path, seamless=False)
    assert probe(out).duration == pytest.approx(20.0, abs=0.1)


def test_intro_outro(video_5s: Path, tmp_path: Path) -> None:
    out = tmp_path / "with_intro.mp4"
    build_looped_video(video_5s, 30.0, out, FAST, work_dir=tmp_path, intro=video_5s, outro=video_5s)
    assert probe(out).duration == pytest.approx(30.0, abs=0.15)


def test_intro_longer_than_video_fails(video_5s: Path, tmp_path: Path) -> None:
    with pytest.raises(LooperError, match="длиннее"):
        build_looped_video(
            video_5s,
            8.0,
            out := tmp_path / "x.mp4",
            FAST,
            work_dir=tmp_path,
            intro=video_5s,
            outro=video_5s,
        )
        assert not out.exists()


def test_effects_chain_renders(video_5s: Path, tmp_path: Path) -> None:
    chain = compose(
        ken_burns(640, 360, duration=4.0, fps=30.0), lut("warm"), vignette(0.4), film_grain(6)
    )
    out = tmp_path / "fx.mp4"
    build_looped_video(video_5s, 12.0, out, FAST, work_dir=tmp_path, post_vf=chain)
    info = probe(out)
    assert info.duration == pytest.approx(12.0, abs=0.1)
    assert (info.width, info.height) == (640, 360)


def test_overlay_screen_blend(video_5s: Path, tmp_path: Path) -> None:
    overlay = OverlaySpec(path=video_5s, opacity=0.3, screen_blend=True)
    out = tmp_path / "ovl.mp4"
    build_looped_video(video_5s, 10.0, out, FAST, work_dir=tmp_path, overlay=overlay)
    assert probe(out).duration == pytest.approx(10.0, abs=0.1)


def test_unknown_lut_rejected() -> None:
    with pytest.raises(ValueError, match="Неизвестный фильтр"):
        lut("рентген")


def test_full_disk_stops_before_concat(
    video_5s: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мест нет — понятная ошибка ДО многочасовой склейки, а не крах на 85 %."""
    import os
    import shutil as shutil_module

    from core.video.looper import DiskSpaceError

    real_usage = shutil_module.disk_usage

    def tiny_disk(path: int | str | os.PathLike[str]) -> object:
        usage = real_usage(path)
        return type(usage)(usage.total, usage.total - 1024, 1024)  # свободно 1 КБ

    monkeypatch.setattr("core.video.looper.shutil.disk_usage", tiny_disk)
    with pytest.raises(DiskSpaceError, match="Недостаточно места"):
        build_looped_video(video_5s, 60.0, tmp_path / "x.mp4", FAST, work_dir=tmp_path)
