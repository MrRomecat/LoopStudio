"""Волна 3, ядро: анализ тишины/громкости и умное перемешивание."""

from __future__ import annotations

import itertools
import random
import subprocess
from pathlib import Path

import pytest

from core.audio.analyze import _parse_loudnorm_json, _parse_silence_edges, analyze_track
from core.audio.shuffle import smart_shuffle
from core.media.ffmpeg import find_ffmpeg
from core.media.probe import probe


@pytest.fixture(scope="session")
def track_with_silence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """3 c синуса + 2 c тишины в хвосте (типичный «хвост от артиста»)."""
    out = tmp_path_factory.mktemp("wave3") / "с_хвостом.wav"
    cmd = [
        str(find_ffmpeg()),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-af",
        "apad=pad_dur=2",
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def test_analyze_detects_tail_silence(track_with_silence: Path) -> None:
    duration = probe(track_with_silence).duration
    analysis = analyze_track(track_with_silence, duration)
    assert analysis.lead_silence == 0.0
    # хвост ~2 c минус естественные 0.3 c
    assert 1.4 <= analysis.tail_silence <= 1.9
    start, end = analysis.trimmed_bounds
    assert start == 0.0
    assert 3.0 <= end <= 3.7
    assert analysis.loudness is not None
    assert -40.0 < analysis.loudness.input_i < 0.0


def test_analyze_clean_track(music_dir: Path) -> None:
    """Трек без пауз не обрезается."""
    track = music_dir / "01.wav"
    duration = probe(track).duration
    analysis = analyze_track(track, duration)
    assert analysis.lead_silence == 0.0
    assert analysis.tail_silence == 0.0
    assert analysis.trimmed_duration == pytest.approx(duration)


def test_parse_edges_never_degenerates() -> None:
    """Почти полностью тихий файл не вырождается в нулевую длительность."""
    stderr = "silence_start: 0.0\nsilence_end: 4.9 | silence_duration: 4.9\n"
    lead, tail = _parse_silence_edges(stderr, 5.0)
    assert (lead, tail) == (0.0, 0.0)


def test_parse_loudnorm_json() -> None:
    stderr = (
        "всякий вывод\n[Parsed_loudnorm_1 @ 0x1] \n{\n"
        '\t"input_i" : "-23.53",\n\t"input_tp" : "-4.10",\n'
        '\t"input_lra" : "6.30",\n\t"input_thresh" : "-33.72",\n'
        '\t"output_i" : "-14.0",\n\t"target_offset" : "0.3"\n}\n'
    )
    stats = _parse_loudnorm_json(stderr)
    assert stats is not None
    assert stats.input_i == pytest.approx(-23.53)
    assert stats.input_tp == pytest.approx(-4.1)
    assert _parse_loudnorm_json("нет json") is None


# --- перемешивание -------------------------------------------------------------


def _no_adjacent(names: list[str], artist_of: dict[str, str | None]) -> bool:
    seq = [artist_of[n] for n in names]
    pairs = list(itertools.pairwise(seq))
    if len(seq) > 2:
        pairs.append((seq[-1], seq[0]))  # стык круга плейлиста
    return all(a is None or b is None or a.lower() != b.lower() for a, b in pairs)


def test_shuffle_separates_artists() -> None:
    tracks: list[tuple[str, str | None]] = [
        ("a1.mp3", "Kupla"),
        ("a2.mp3", "Kupla"),
        ("a3.mp3", "Kupla"),
        ("b1.mp3", "Aso"),
        ("b2.mp3", "Aso"),
        ("b3.mp3", "Aso"),
        ("c1.mp3", None),
    ]
    artist_of = dict(tracks)
    rng = random.Random(7)
    for _ in range(20):
        result = smart_shuffle(tracks, rng=rng)
        assert sorted(result) == sorted(n for n, _ in tracks)
        assert _no_adjacent(result, artist_of)


def test_shuffle_keep_first() -> None:
    tracks: list[tuple[str, str | None]] = [
        ("опенер.mp3", "Star"),
        ("x.mp3", "A"),
        ("y.mp3", "B"),
        ("z.mp3", "A"),
    ]
    rng = random.Random(1)
    for _ in range(10):
        result = smart_shuffle(tracks, keep_first=True, rng=rng)
        assert result[0] == "опенер.mp3"


def test_shuffle_impossible_case_returns_something() -> None:
    tracks: list[tuple[str, str | None]] = [("a.mp3", "X"), ("b.mp3", "X"), ("c.mp3", "X")]
    result = smart_shuffle(tracks, rng=random.Random(3))
    assert sorted(result) == ["a.mp3", "b.mp3", "c.mp3"]
