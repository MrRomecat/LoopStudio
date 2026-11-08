"""Этап 2: натуральная сортировка и резиновый таймлайн (чистые юнит-тесты)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.audio.playlist import scan_music_folder, sort_tracks
from core.audio.timeline import TimelineError, build_timeline

TRACKS = [(Path("01.wav"), 3.0), (Path("02.wav"), 4.0), (Path("03.wav"), 5.0)]


def _names(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


class TestSortTracks:
    def test_spec_table(self) -> None:
        """Таблица из дорожной карты: ["10","2","b","03","a"] → [2, 03, 10, a, b]."""
        paths = [Path(f"{n}.wav") for n in ("10", "2", "b", "03", "a")]
        assert _names(sort_tracks(paths)) == ["2.wav", "03.wav", "10.wav", "a.wav", "b.wav"]

    def test_numeric_by_value_not_string(self) -> None:
        paths = [Path("100.mp3"), Path("20.mp3"), Path("3.mp3")]
        assert _names(sort_tracks(paths)) == ["3.mp3", "20.mp3", "100.mp3"]

    def test_track_5_is_not_numeric(self) -> None:
        """«track 5» — нечисловое имя: идёт после чисто числовых."""
        paths = [Path("track 5.wav"), Path("7.wav")]
        assert _names(sort_tracks(paths)) == ["7.wav", "track 5.wav"]

    def test_non_numeric_natural_order(self) -> None:
        """Как в проводнике: track2 < track10."""
        paths = [Path("track10.wav"), Path("track2.wav")]
        assert _names(sort_tracks(paths)) == ["track2.wav", "track10.wav"]

    def test_case_insensitive(self) -> None:
        paths = [Path("Beta.wav"), Path("alpha.wav")]
        assert _names(sort_tracks(paths)) == ["alpha.wav", "Beta.wav"]

    def test_stable_and_pure(self) -> None:
        paths = [Path("2.wav"), Path("1.wav")]
        sort_tracks(paths)
        assert _names(paths) == ["2.wav", "1.wav"], "исходный список не должен меняться"


class TestScanMusicFolder:
    def test_filters_and_sorts(self, tmp_path: Path) -> None:
        for name in ("10.wav", "2.flac", "readme.txt", "cover.jpg", "a.mp3"):
            (tmp_path / name).write_bytes(b"x")
        (tmp_path / "подпапка").mkdir()
        assert _names(scan_music_folder(tmp_path)) == ["2.flac", "10.wav", "a.mp3"]

    def test_missing_folder(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            scan_music_folder(tmp_path / "нет")


class TestBuildTimeline:
    def test_exact_target(self) -> None:
        """target 12 = ровно два круга не нужны: 3+4+5 = 12 → 3 трека."""
        tl = build_timeline(TRACKS, 12.0)
        assert len(tl.entries) == 3
        assert tl.total_duration == pytest.approx(12.0)

    def test_rubber_overshoot(self) -> None:
        """target 20: 3+4+5+3+4+5 = 24 — последний трек доигрывает целиком."""
        tl = build_timeline(TRACKS, 20.0)
        assert len(tl.entries) == 6
        assert tl.total_duration == pytest.approx(24.0)
        last = tl.entries[-1]
        assert last.start + last.duration == pytest.approx(tl.total_duration)

    def test_round_robin_order(self) -> None:
        tl = build_timeline(TRACKS, 20.0)
        assert [e.path.name for e in tl.entries] == [
            "01.wav",
            "02.wav",
            "03.wav",
            "01.wav",
            "02.wav",
            "03.wav",
        ]

    def test_pause_extends_total(self) -> None:
        """target 20, пауза 0.5: старты сдвигаются, итог 21.0 (5 треков)."""
        tl = build_timeline(TRACKS, 20.0, pause=0.5)
        assert len(tl.entries) == 5
        assert tl.total_duration == pytest.approx(21.0)
        assert tl.entries[1].start == pytest.approx(3.5)

    def test_crossfade_shrinks_total(self) -> None:
        """target 20, кроссфейд 1: перекрытия → 7 треков, итог 21.0."""
        tl = build_timeline(TRACKS, 20.0, crossfade=1.0)
        assert len(tl.entries) == 7
        assert tl.total_duration == pytest.approx(21.0)
        assert tl.entries[1].start == pytest.approx(2.0)

    def test_pause_disables_crossfade(self) -> None:
        tl = build_timeline(TRACKS, 20.0, pause=0.5, crossfade=3.0)
        assert tl.crossfade == 0.0
        assert tl.total_duration == pytest.approx(21.0)

    def test_starts_are_chapter_timecodes(self) -> None:
        tl = build_timeline(TRACKS, 12.0, pause=1.0)
        assert [e.start for e in tl.entries] == [
            pytest.approx(0.0),
            pytest.approx(4.0),
            pytest.approx(9.0),
        ]

    def test_empty_playlist(self) -> None:
        with pytest.raises(TimelineError, match="пуст"):
            build_timeline([], 60.0)

    def test_zero_target(self) -> None:
        with pytest.raises(TimelineError, match="больше нуля"):
            build_timeline(TRACKS, 0.0)

    def test_crossfade_longer_than_shortest_track(self) -> None:
        with pytest.raises(TimelineError, match="россфейд"):
            build_timeline(TRACKS, 20.0, crossfade=3.0)
