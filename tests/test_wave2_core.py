"""Волна 2, ядро: батч, кредиты, проверка проекта, профили в настройках."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.media.probe import MediaInfo
from core.render.batch import clone_job_for_folder, list_music_subfolders
from core.render.credits import build_credits_text, unique_tracks
from core.render.job import AmbientConfig, RenderJob
from core.render.project_check import check_project, relink_job, relink_path
from core.settings import AppSettings


def _audio(path: str, artist: str | None = None, title: str | None = None) -> MediaInfo:
    return MediaInfo(
        path=Path(path),
        duration=60.0,
        has_video=False,
        has_audio=True,
        artist=artist,
        title_tag=title,
    )


# --- батч ---------------------------------------------------------------------


def test_list_music_subfolders(tmp_path: Path) -> None:
    (tmp_path / "Дожди").mkdir()
    (tmp_path / "Дожди" / "01.mp3").write_bytes(b"x")
    (tmp_path / "Дожди" / "02.wav").write_bytes(b"x")
    (tmp_path / "Пустая").mkdir()
    (tmp_path / "Камин").mkdir()
    (tmp_path / "Камин" / "огонь.flac").write_bytes(b"x")
    (tmp_path / "просто_файл.txt").write_bytes(b"x")

    folders = list_music_subfolders(tmp_path)
    names = [(f.path.name, f.track_count) for f in folders]
    assert names == [("Дожди", 2), ("Камин", 1)]  # пустая папка не предлагается


def test_clone_job_for_folder() -> None:
    job = RenderJob(
        video_path="v.mp4",
        music_dir="C:/музыка/старая",
        track_order=["01.wav", "02.wav"],
        title_template="Rain {date}",
        duration_sec=7200.0,
    )
    clone = clone_job_for_folder(job, Path("C:/музыка/новая"))
    assert clone.music_dir == str(Path("C:/музыка/новая"))
    assert clone.track_order == []  # ручной порядок привязан к старой папке
    assert clone.title_template == "Rain {date} — {folder}"  # файлы различимы
    assert clone.duration_sec == 7200.0
    # исходное задание не тронуто
    assert job.music_dir == "C:/музыка/старая" and job.track_order == ["01.wav", "02.wav"]

    with_folder = RenderJob(title_template="Sleep {folder} mix")
    assert clone_job_for_folder(with_folder, Path("C:/m")).title_template == "Sleep {folder} mix"


# --- кредиты ------------------------------------------------------------------


def test_credits_grouped_and_untagged() -> None:
    tracks = [
        _audio("C:/m/01 - Midnight.mp3", artist="Kupla", title="Midnight"),
        _audio("C:/m/02 - Waves.mp3", artist="Aso", title="Waves"),
        _audio("C:/m/03 - Dawn.mp3", artist="Kupla", title="Dawn"),
        _audio("C:/m/04 без тегов.mp3"),
    ]
    text = build_credits_text(tracks)
    assert "Kupla — Midnight" in text
    assert "Kupla — Dawn" in text
    assert "Aso — Waves" in text
    assert "не заполнен тег исполнителя" in text
    assert "04 без тегов.mp3" in text
    assert text.index("Aso — Waves") < text.index("Kupla — Midnight")  # по алфавиту


def test_unique_tracks_keeps_first_occurrence() -> None:
    a = _audio("C:/m/a.mp3")
    b = _audio("C:/m/b.mp3")
    playlist = [(a.path, a), (b.path, b), (a.path, a)]  # плейлист зациклен
    assert [t.path.name for t in unique_tracks(playlist)] == ["a.mp3", "b.mp3"]


# --- проверка проекта ---------------------------------------------------------


def test_check_project_reports_missing_and_diff(tmp_path: Path) -> None:
    music = tmp_path / "музыка"
    music.mkdir()
    (music / "01.wav").write_bytes(b"x")
    (music / "03 новый.wav").write_bytes(b"x")
    video = tmp_path / "фон.mp4"
    video.write_bytes(b"x")

    job = RenderJob(
        video_path=str(video),
        intro_path=str(tmp_path / "нет_такого_интро.mp4"),
        music_dir=str(music),
        track_order=["01.wav", "02 пропавший.wav"],
        ambient=[AmbientConfig(enabled=True, path=str(tmp_path / "пропал.flac"))],
    )
    report = check_project(job)
    assert not report.clean
    labels = [m.label for m in report.missing]
    assert "Интро (шаг 1)" in labels
    assert "Эмбиент-слой 1 (шаг 3)" in labels
    assert "Основное видео (шаг 1)" not in labels  # существует
    assert report.playlist.missing == ("02 пропавший.wav",)
    assert report.playlist.added == ("03 новый.wav",)


def test_relink_after_move(tmp_path: Path) -> None:
    """Переезд библиотеки: старые пути чинятся одной операцией по хвосту пути."""
    old_root = tmp_path / "старый_диск"
    new_root = tmp_path / "новый_диск"
    (new_root / "Библиотека" / "Видео").mkdir(parents=True)
    (new_root / "Библиотека" / "Звуки").mkdir(parents=True)
    (new_root / "Библиотека" / "Видео" / "море.mp4").write_bytes(b"x")
    (new_root / "Библиотека" / "Звуки" / "дождь.flac").write_bytes(b"x")
    (new_root / "Библиотека" / "Музыка" / "пак1").mkdir(parents=True)
    (new_root / "Библиотека" / "Музыка" / "пак1" / "01.wav").write_bytes(b"x")

    job = RenderJob(
        video_path=str(old_root / "Библиотека" / "Видео" / "море.mp4"),
        music_dir=str(old_root / "Библиотека" / "Музыка" / "пак1"),
        track_order=["01.wav"],
        ambient=[
            AmbientConfig(enabled=True, path=str(old_root / "Библиотека" / "Звуки" / "дождь.flac"))
        ],
    )
    fixed_job, fixed, still_missing = relink_job(job, new_root)
    assert still_missing == []
    assert len(fixed) == 3
    assert Path(fixed_job.video_path).is_file()
    assert Path(fixed_job.music_dir).is_dir()
    assert Path(fixed_job.ambient[0].path).is_file()
    assert fixed_job.track_order == ["01.wav"]  # порядок пережил переезд
    assert relink_path(Path("C:/нигде/нет.mp4"), new_root) is None


# --- настройки: профили и недавние --------------------------------------------


def test_settings_profiles_and_recents_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.settings as settings_module

    # изолируем settings_dir от реального APPDATA
    monkeypatch.setattr(settings_module, "settings_dir", lambda: tmp_path)

    settings = AppSettings()
    settings.channel_profiles["Rain"] = {"resolution": "2160p"}
    for i in range(12):
        settings.remember_project(f"C:/проекты/канал{i}.lsproj")
    settings.remember_project("C:/проекты/канал5.lsproj")  # дубль → наверх
    settings.save()

    loaded = AppSettings.load()
    assert loaded.channel_profiles == {"Rain": {"resolution": "2160p"}}
    assert len(loaded.recent_projects) == 10
    assert loaded.recent_projects[0] == "C:/проекты/канал5.lsproj"
    assert loaded.recent_projects.count("C:/проекты/канал5.lsproj") == 1
