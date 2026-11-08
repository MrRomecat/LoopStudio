"""Волна 3, движок: плавный старт (аудио+видео), трим тишины, точная нормализация."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from core.audio.analyze import LoudnessStats
from core.audio.mixdown import AmbientLayer, MixdownSettings, build_master
from core.audio.timeline import build_timeline
from core.media.ffmpeg import run_ffmpeg
from core.media.probe import probe
from core.video.looper import VideoEncodeSettings, build_looped_video


def _rms(path: Path, tmp_path: Path, start: float, duration: float, name: str) -> float:
    raw = tmp_path / f"{name}.raw"
    run_ffmpeg(
        [
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            str(raw),
        ]
    )
    samples = struct.unpack(f"<{raw.stat().st_size // 2}h", raw.read_bytes())
    return float((sum(s * s for s in samples) / len(samples)) ** 0.5)


def test_smooth_start_fades_in_music_and_ambient(music_dir: Path, tmp_path: Path) -> None:
    """Плавный старт: первые полсекунды заметно тише середины ролика."""
    tracks = [(p, probe(p).duration) for p in sorted(music_dir.iterdir())]
    tl = build_timeline(tracks, 12.0)
    out = tmp_path / "master.flac"
    build_master(
        tl,
        MixdownSettings(intro_fade_in=2.0, ambient_fade_in=2.0),
        [AmbientLayer(path=music_dir / "02.wav", volume=0.5)],
        out,
        work_dir=tmp_path,
    )
    head = _rms(out, tmp_path, 0.0, 0.4, "head")
    body = _rms(out, tmp_path, 5.0, 2.0, "body")
    assert head < body * 0.35, f"fade-in не слышен: head={head:.0f}, body={body:.0f}"
    assert probe(out).duration == pytest.approx(tl.total_duration, abs=0.1)


def test_pause_fade_in_keeps_duration(music_dir: Path, tmp_path: Path) -> None:
    """Мягкое вступление после пауз не меняет длительность мастера."""
    tracks = [(p, probe(p).duration) for p in sorted(music_dir.iterdir())]
    tl = build_timeline(tracks, 15.0, pause=1.0)
    out = tmp_path / "master.flac"
    build_master(tl, MixdownSettings(pause_fade_in=1.0), [], out, work_dir=tmp_path)
    assert probe(out).duration == pytest.approx(tl.total_duration, abs=0.1)


def test_edge_trims_apply(music_dir: Path, tmp_path: Path) -> None:
    """atrim краёв: длительность мастера равна сумме УРЕЗАННЫХ треков."""
    track = music_dir / "03.wav"  # 5 c
    trimmed = (0.5, 4.5)  # остаётся 4 c
    tl = build_timeline([(track, 4.0)], 7.0)  # 2 повтора × 4 c = 8 c
    assert tl.total_duration == pytest.approx(8.0)
    out = tmp_path / "master.flac"
    build_master(tl, MixdownSettings(edge_trims={track: trimmed}), [], out, work_dir=tmp_path)
    assert probe(out).duration == pytest.approx(8.0, abs=0.1)


def test_precise_normalization_graph(music_dir: Path, tmp_path: Path) -> None:
    """Точная нормализация (measured, linear) собирается и держит длительность."""
    tracks = [(p, probe(p).duration) for p in sorted(music_dir.iterdir())]
    tl = build_timeline(tracks, 12.0)
    stats = {
        p: LoudnessStats(input_i=-20.0, input_tp=-3.0, input_lra=4.0, input_thresh=-30.0)
        for p, _ in tracks
    }
    out = tmp_path / "master.flac"
    build_master(tl, MixdownSettings(normalize=True, loudness=stats), [], out, work_dir=tmp_path)
    assert probe(out).duration == pytest.approx(tl.total_duration, abs=0.1)


def _mean_gray(video: Path, tmp_path: Path, ts: float, name: str) -> float:
    raw = tmp_path / f"{name}.gray"
    run_ffmpeg(
        [
            "-ss",
            str(ts),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=64:36",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            str(raw),
        ]
    )
    data = raw.read_bytes()
    return sum(data) / len(data)


def test_excluded_tracks_and_start_preview(
    video_5s: Path, music_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Исключённые треки не рендерятся; «Начало ролика» не трогает random."""
    from core.render.job import JobError, RenderJob
    from core.render.pipeline import _ordered_tracks, render_job

    def no_random(*args: object) -> int:
        raise AssertionError("preview_from_start не должен звать random")

    monkeypatch.setattr("core.render.pipeline.random.randrange", no_random)

    job = RenderJob(
        video_path=str(video_5s),
        music_dir=str(music_dir),
        output_dir=str(tmp_path / "out"),
        duration_sec=60.0,
        excluded_tracks=["02.wav", "03.wav"],
    )
    result = render_job(job, preview=True, preview_from_start=True)
    lines = result.tracklist_path.read_text(encoding="utf-8").splitlines()
    assert all(line.endswith(" 01") for line in lines), lines  # только трек 01

    all_excluded = RenderJob(
        music_dir=str(music_dir), excluded_tracks=["01.wav", "02.wav", "03.wav"]
    )
    with pytest.raises(JobError, match="исключены"):
        _ordered_tracks(all_excluded)


def test_seams_preview_builds(video_5s: Path, tmp_path: Path) -> None:
    """Нарезка швов: секции лупа и интро собираются в один просмотровый файл."""
    from core.render.job import RenderJob
    from core.render.seams import build_seams_preview

    job = RenderJob(video_path=str(video_5s), intro_path=str(video_5s))
    out = tmp_path / "швы.mp4"
    build_seams_preview(job, out)
    info = probe(out)
    assert info.has_video and not info.has_audio
    # 2×(шов лупа) + разрыв + секция интро: заметно длиннее одного куска
    assert info.duration > 10.0


def test_video_smooth_start_first_frame_dark(video_5s: Path, tmp_path: Path) -> None:
    """Плавный старт без интро: ролик начинается из чёрного, стык лупа честный."""
    settings = VideoEncodeSettings(width=320, height=180, fps=30.0, preset="veryfast", crf=30)
    out = tmp_path / "loop.mp4"
    build_looped_video(
        video_5s,
        14.0,
        out,
        settings,
        work_dir=tmp_path,
        seamless=True,
        crossfade=1.0,
        start_fade=1.5,
    )
    assert probe(out).duration == pytest.approx(14.0, abs=0.2)
    first = _mean_gray(out, tmp_path, 0.05, "first")
    later = _mean_gray(out, tmp_path, 6.0, "later")
    assert first < later * 0.35, f"начало не из чёрного: first={first:.0f}, later={later:.0f}"
