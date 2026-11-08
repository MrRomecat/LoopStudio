"""Автопроверка готового файла: целый проходит, битый и обрезанный — нет."""

from __future__ import annotations

from pathlib import Path

from core.render.verify import verify_output


def test_intact_file_passes(video_5s: Path) -> None:
    report = verify_output(video_5s, 5.0)
    assert report.ok, report.problems
    assert report.file_duration is not None
    assert len(report.checks) == 3
    assert "ПРОВЕРЕНО" in report.summary


def test_duration_mismatch_fails(video_5s: Path) -> None:
    report = verify_output(video_5s, 100.0)
    assert not report.ok
    assert any("не совпадает" in p for p in report.problems)
    assert "НЕ ПРОЙДЕНА" in report.summary


def test_garbage_file_fails(tmp_path: Path) -> None:
    fake = tmp_path / "битый.mp4"
    fake.write_bytes(b"\x00" * 4096)
    report = verify_output(fake, 5.0)
    assert not report.ok


def test_truncated_file_fails(video_5s: Path, tmp_path: Path) -> None:
    """Оборванная запись (перезагрузка на финализации) не проходит проверку."""
    data = video_5s.read_bytes()
    cut = tmp_path / "оборванный.mp4"
    cut.write_bytes(data[: len(data) // 2])
    report = verify_output(cut, 5.0)
    assert not report.ok
