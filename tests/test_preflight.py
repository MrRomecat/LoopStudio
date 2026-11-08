"""Пре-флайт: смета, прогноз длительности, тихие конфликты, место на дисках."""

from __future__ import annotations

from collections import namedtuple
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.media.probe import MediaInfo
from core.render.job import EffectsConfig, RenderJob
from core.render.preflight import run_preflight

_Usage = namedtuple("_Usage", "total used free")

_TB = 1024**4
_VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".avi")


def _fake_probe(
    video_size: tuple[int, int] = (1920, 1080),
    track_duration: float = 40.0,
    video_duration: float = 12.0,
) -> Callable[[Path], MediaInfo]:
    """probe-заглушка: видео с заданными размерами, всё остальное — аудио."""

    def probe_fn(path: Path) -> MediaInfo:
        if path.suffix.lower() in _VIDEO_SUFFIXES:
            return MediaInfo(
                path=path,
                duration=video_duration,
                has_video=True,
                has_audio=True,
                width=video_size[0],
                height=video_size[1],
                fps=30.0,
            )
        return MediaInfo(path=path, duration=track_duration, has_video=False, has_audio=True)

    return probe_fn


def _job(music_dir: Path, out: Path, **kwargs: Any) -> RenderJob:
    defaults: dict[str, Any] = {
        "video_path": "C:/видео/фон.mp4",
        "music_dir": str(music_dir),
        "output_dir": str(out),
        "duration_sec": 120.0,
    }
    defaults.update(kwargs)
    return RenderJob(**defaults)


def _rich_disk(path: Path) -> _Usage:
    return _Usage(_TB, 0, _TB)


def test_clean_run_predicts_duration_and_sizes(music_dir: Path, tmp_path: Path) -> None:
    job = _job(music_dir, tmp_path / "out")
    report = run_preflight(job, probe_fn=_fake_probe(), disk_usage=_rich_disk)
    assert report.clean, (report.warnings, report.blockers)
    # резиновый таймлайн: 3 трека по 40 с → 120 с ровно
    assert report.predicted_duration == 120.0
    assert report.est_output_bytes is not None and report.est_output_bytes > 0
    assert report.est_temp_bytes is not None
    assert report.est_temp_bytes > report.est_output_bytes
    assert any("Итоговая длительность" in line for line in report.infos)
    assert (tmp_path / "out").is_dir(), "проверка записываемости создаёт папку"


def test_intro_does_not_extend_prediction(music_dir: Path, tmp_path: Path) -> None:
    """Интро не удлиняет ролик: видеоряд собирается ровно в длину мастера."""
    job = _job(music_dir, tmp_path / "out", intro_path="C:/видео/интро.mp4")
    report = run_preflight(job, probe_fn=_fake_probe(), disk_usage=_rich_disk)
    assert report.predicted_duration == 120.0


def test_long_intro_blocker(music_dir: Path, tmp_path: Path) -> None:
    """Интро+аутро длиннее ролика ловятся ДО рендера, а не после аудио-мастера."""
    job = _job(
        music_dir,
        tmp_path / "out",
        duration_sec=60.0,
        intro_path="C:/видео/интро.mp4",
        outro_path="C:/видео/аутро.mp4",
    )
    # ролик 63 c (9 треков × 7 c), интро+аутро 2×12 c = 24 c < 63 — чисто
    ok = run_preflight(
        job, probe_fn=_fake_probe(track_duration=7.0, video_duration=12.0), disk_usage=_rich_disk
    )
    assert ok.predicted_duration == 63.0
    assert not ok.blockers

    # тот же ролик, но клипы по 40 c: 80 c >= 63 c — блокер до рендера
    report = run_preflight(
        job, probe_fn=_fake_probe(track_duration=7.0, video_duration=40.0), disk_usage=_rich_disk
    )
    assert any("длиннее всего ролика" in b for b in report.blockers)


def test_silent_conflicts_warned(music_dir: Path, tmp_path: Path) -> None:
    job = _job(
        music_dir,
        tmp_path / "out",
        pause=2.0,
        crossfade=3.0,
        effects=EffectsConfig(overlay_path="C:/фх/снег.mov"),
    )
    report = run_preflight(
        job, probe_fn=_fake_probe(video_size=(1080, 1920)), disk_usage=_rich_disk
    )
    text = "\n".join(report.warnings)
    assert "отключает кроссфейд" in text
    assert "вертикальное" in text
    assert ".mov" in text
    assert not report.clean


def test_disk_space_blocker(music_dir: Path, tmp_path: Path) -> None:
    def poor_disk(path: Path) -> _Usage:
        return _Usage(_TB, _TB, 10 * 1024**2)  # свободно 10 МБ

    job = _job(music_dir, tmp_path / "out", duration_sec=3600.0)
    report = run_preflight(job, probe_fn=_fake_probe(), disk_usage=poor_disk)
    assert any("не хватит места" in b for b in report.blockers)


def test_pending_queue_space_warning(music_dir: Path, tmp_path: Path) -> None:
    free = 30 * 1024**3  # 30 ГБ: текущему заданию хватает, очереди из 4К-гигантов — нет

    def medium_disk(path: Path) -> _Usage:
        return _Usage(_TB, 0, free)

    job = _job(music_dir, tmp_path / "out", duration_sec=120.0)
    pending = [
        RenderJob(resolution="2160p", quality="source", duration_sec=36_000.0) for _ in range(2)
    ]
    report = run_preflight(job, pending=pending, probe_fn=_fake_probe(), disk_usage=medium_disk)
    assert not report.blockers
    assert any("уже ожидающими в очереди" in w for w in report.warnings)


def test_unwritable_output_dir(music_dir: Path, tmp_path: Path) -> None:
    fake_file = tmp_path / "занято.txt"
    fake_file.write_text("файл вместо папки", encoding="utf-8")
    job = _job(music_dir, fake_file)
    report = run_preflight(job, probe_fn=_fake_probe(), disk_usage=_rich_disk)
    assert any("не получается записать" in b for b in report.blockers)


def test_playlist_drift_warning(music_dir: Path, tmp_path: Path) -> None:
    """Сохранённый порядок разошёлся с папкой — предупреждение до рендера."""
    job = _job(
        music_dir,
        tmp_path / "out",
        track_order=["01.wav", "стёртый.wav"],  # 02/03 станут «новыми», один пропал
    )
    report = run_preflight(job, probe_fn=_fake_probe(), disk_usage=_rich_disk)
    assert any("Плейлист изменился" in w for w in report.warnings)


def test_empty_music_folder_blocker(tmp_path: Path) -> None:
    empty = tmp_path / "пусто"
    empty.mkdir()
    job = _job(empty, tmp_path / "out")
    report = run_preflight(job, probe_fn=_fake_probe(), disk_usage=_rich_disk)
    assert any("нет поддерживаемых треков" in b for b in report.blockers)
