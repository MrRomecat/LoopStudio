"""Этап 2: master.flac собирается и имеет расчётную длительность ±0.1 с (критерий этапа)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.audio.mixdown import AmbientLayer, MixdownSettings, build_master
from core.audio.playlist import scan_music_folder
from core.audio.timeline import Timeline, build_timeline
from core.media.probe import probe


def _timeline(music_dir: Path, target: float, **kwargs: float) -> Timeline:
    tracks = [(p, probe(p).duration) for p in scan_music_folder(music_dir)]
    return _build(tracks, target, **kwargs)


def _build(tracks: list[tuple[Path, float]], target: float, **kwargs: float) -> Timeline:
    return build_timeline(tracks, target, **kwargs)


def _master_duration(
    timeline: Timeline,
    tmp_path: Path,
    settings: MixdownSettings | None = None,
    ambient: list[AmbientLayer] | None = None,
) -> float:
    out = tmp_path / "master.flac"
    build_master(timeline, settings or MixdownSettings(), ambient or [], out, work_dir=tmp_path)
    return probe(out).duration


def test_plain_concat(music_dir: Path, tmp_path: Path) -> None:
    tl = _timeline(music_dir, 20.0)
    assert tl.total_duration == pytest.approx(24.0)
    assert _master_duration(tl, tmp_path) == pytest.approx(24.0, abs=0.1)


def test_with_pauses_and_fadeout(music_dir: Path, tmp_path: Path) -> None:
    tl = _timeline(music_dir, 20.0, pause=0.5)
    assert tl.total_duration == pytest.approx(21.0)
    settings = MixdownSettings(fade_out=0.5)
    assert _master_duration(tl, tmp_path, settings) == pytest.approx(21.0, abs=0.1)


def test_with_crossfade(music_dir: Path, tmp_path: Path) -> None:
    tl = _timeline(music_dir, 20.0, crossfade=1.0)
    assert tl.total_duration == pytest.approx(21.0)
    assert _master_duration(tl, tmp_path) == pytest.approx(21.0, abs=0.1)


def test_with_ambient_layers(music_dir: Path, tmp_path: Path) -> None:
    tl = _timeline(music_dir, 12.0)
    ambient = [
        AmbientLayer(path=music_dir / "01.wav", volume=0.4, loop_crossfade=1.0),
        AmbientLayer(path=music_dir / "02.wav", volume=0.2, loop_crossfade=0.0),
    ]
    assert _master_duration(tl, tmp_path, ambient=ambient) == pytest.approx(12.0, abs=0.1)


def test_with_normalization(music_dir: Path, tmp_path: Path) -> None:
    tl = _timeline(music_dir, 12.0)
    settings = MixdownSettings(normalize=True, music_volume=0.8)
    assert _master_duration(tl, tmp_path, settings) == pytest.approx(12.0, abs=0.1)


def test_source_audio_layer_is_audible(
    video_with_metadata: Path, music_dir: Path, tmp_path: Path
) -> None:
    """Звук исходного видео (слой из mp4) реально слышен в мастере.

    Музыка на нуле, слой на полной громкости: если бы слой терялся,
    мастер был бы тишиной и RMS был бы ~0.
    """
    import struct

    from core.media.ffmpeg import run_ffmpeg

    tl = _timeline(music_dir, 12.0)
    out = tmp_path / "master.flac"
    build_master(
        tl,
        MixdownSettings(music_volume=0.0),
        [AmbientLayer(path=video_with_metadata, volume=1.0, loop_crossfade=1.0)],
        out,
        work_dir=tmp_path,
    )
    raw = tmp_path / "master.raw"
    run_ffmpeg(["-i", str(out), "-ac", "1", "-ar", "8000", "-f", "s16le", str(raw)])
    samples = struct.unpack(f"<{raw.stat().st_size // 2}h", raw.read_bytes())
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    assert rms > 1000, f"слой исходного звука не слышен (RMS={rms:.0f})"


def test_source_layer_survives_many_loops(
    video_short_audio: Path, music_dir: Path, tmp_path: Path
) -> None:
    """Регресс кейса «море.mp4»: аудио короче контейнера, кроссфейд 3 с,
    ~50 повторов юнита — слой должен звучать ДО КОНЦА ролика.

    Старый путь (-stream_loop на юните) падал с -22 посреди длинного мастера.
    """
    import struct

    from core.media.ffmpeg import run_ffmpeg

    tl = _timeline(music_dir, 120.0)
    out = tmp_path / "master.flac"
    build_master(
        tl,
        MixdownSettings(music_volume=0.0),
        [AmbientLayer(path=video_short_audio, volume=1.0, loop_crossfade=3.0)],
        out,
        work_dir=tmp_path,
    )
    assert probe(out).duration == pytest.approx(tl.total_duration, abs=0.15)
    # последние 10 секунд не должны быть тишиной (слой лупится весь ролик)
    raw = tmp_path / "tail.raw"
    run_ffmpeg(
        [
            "-ss",
            str(tl.total_duration - 10),
            "-i",
            str(out),
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
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    assert rms > 1000, f"слой заглох до конца ролика (RMS хвоста={rms:.0f})"


def test_single_track_repeated(music_dir: Path, tmp_path: Path) -> None:
    """Один файл в плейлисте лупится через asplit — не через сотни входов."""
    track = music_dir / "03.wav"
    tl = _build([(track, probe(track).duration)], 18.0)
    assert len(tl.entries) == 4
    assert _master_duration(tl, tmp_path) == pytest.approx(20.0, abs=0.1)
