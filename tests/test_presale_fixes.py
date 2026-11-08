"""Регресс-тесты предпродажного аудита (v1.4.3): каждая находка закреплена."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from core.audio.playlist import natural_key, sort_tracks
from core.audio.shuffle import smart_shuffle
from core.fsio import atomic_write_text
from core.render.batch import clone_job_for_folder
from core.render.job import JobError, RenderJob
from core.render.pipeline import _sanitize_filename
from core.settings import AppSettings
from modules.loopstudio.screen import LoopStudioScreen

# --- мёртвые кнопки шага 10 (критичная находка UI-аудита) ---------------------


def test_preview_start_and_seams_buttons_wired(qtbot: QtBot) -> None:
    """«Начало ролика» и «Проверить швы» обязаны излучать свои сигналы."""
    screen = LoopStudioScreen(AppSettings())
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.preview_start_clicked, timeout=1000):
        screen.preview_start_btn.click()
    with qtbot.waitSignal(screen.seams_clicked, timeout=1000):
        screen.seams_btn.click()


# --- пакетный режим: исключения треков не переносятся на чужие папки ----------


def test_batch_clone_resets_excluded_tracks(tmp_path: Path) -> None:
    base = RenderJob(
        music_dir="C:/music/A", track_order=["01.mp3", "02.mp3"], excluded_tracks=["01.mp3"]
    )
    clone = clone_job_for_folder(base, tmp_path / "B")
    assert clone.track_order == []
    assert clone.excluded_tracks == []  # имена «01.mp3» есть в каждой папке


# --- from_dict: битые .lsproj не роняют приложение ----------------------------


def test_from_dict_structural_garbage_raises_joberror() -> None:
    with pytest.raises(JobError):
        RenderJob.from_dict({"ambient": {"x": 1}})
    with pytest.raises(JobError):
        RenderJob.from_dict({"ambient": ["строка"]})
    with pytest.raises(JobError):
        RenderJob.from_dict({"effects": []})
    with pytest.raises(JobError):
        RenderJob.from_dict("не словарь")  # type: ignore[arg-type]


def test_from_dict_wrong_scalar_types_fall_back_to_defaults() -> None:
    job = RenderJob.from_dict(
        {
            "duration_sec": "3600",  # строка вместо числа
            "music_volume": True,  # bool вместо float
            "track_order": [1, 2, 3],  # числа вместо имён
            "video_path": 42,
            "pause": 2,  # int вместо float — легально, приводится
        }
    )
    assert job.duration_sec == RenderJob().duration_sec
    assert job.music_volume == RenderJob().music_volume
    assert job.track_order == []
    assert job.video_path == ""
    assert job.pause == 2.0 and isinstance(job.pause, float)


# --- атомарная запись ---------------------------------------------------------


def test_atomic_write_text_no_tmp_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_write_text(target, '{"a": 1}')
    atomic_write_text(target, '{"a": 2}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}
    assert list(tmp_path.glob("*.tmp")) == []


def test_renderjob_save_is_atomic_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "проект.lsproj"
    job = RenderJob(duration_sec=120.0, title_template="Тест {date}")
    job.save(path)
    assert RenderJob.load(path).duration_sec == 120.0
    assert list(tmp_path.glob("*.tmp")) == []


# --- настройки: повреждение не молчит и не роняет -----------------------------


def test_settings_corrupt_json_is_preserved_for_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.settings.settings_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("{битый json", encoding="utf-8")
    settings = AppSettings.load()
    assert settings.recent_projects == []
    assert (tmp_path / "settings.json.corrupt").is_file()  # улика сохранена


def test_settings_wrong_field_types_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.settings.settings_dir", lambda: tmp_path)
    payload = {"mix_presets": None, "recent_projects": "не список", "last_output_dir": "C:/ok"}
    (tmp_path / "settings.json").write_text(json.dumps(payload), encoding="utf-8")
    settings = AppSettings.load()
    assert settings.mix_presets == {}
    assert settings.recent_projects == []
    assert settings.last_output_dir == "C:/ok"


def test_settings_json_list_root_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.settings.settings_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert AppSettings.load().recent_projects == []


# --- имена файлов Windows -----------------------------------------------------


def test_sanitize_filename_reserved_and_length() -> None:
    assert _sanitize_filename("con") == "LoopStudio con"
    assert _sanitize_filename("NUL.mp4") == "LoopStudio NUL.mp4"
    assert _sanitize_filename("обычное имя") == "обычное имя"
    assert len(_sanitize_filename("х" * 400)) <= 120


# --- сортировка: юникод-«цифры» не роняют плейлист ----------------------------


def test_natural_key_unicode_digits_no_crash(tmp_path: Path) -> None:
    assert natural_key("трек²")  # isdigit=True у «²», int() бы упал
    paths = [tmp_path / "трек².mp3", tmp_path / "01.mp3"]
    assert len(sort_tracks(paths)) == 2


# --- лимитер мастера: запас 0.97 не аннулируется авто-уровнем -----------------


def test_alimiter_has_level_zero_pinned() -> None:
    source = (Path(__file__).parents[1] / "core" / "audio" / "mixdown.py").read_text(
        encoding="utf-8"
    )
    assert source.count("alimiter=limit=0.97:level=0") == 2, (
        "закон мастера: alimiter обязан идти с level=0 в обоих проходах "
        "(авто-уровень молча съедает запас громкости)"
    )


# --- умное перемешивание ------------------------------------------------------


def test_shuffle_unfixable_set_returns_quickly_and_completely() -> None:
    tracks = [(f"{i:02d}.mp3", "Один Артист") for i in range(40)]
    result = smart_shuffle(tracks)
    assert sorted(result) == sorted(name for name, _ in tracks)


def test_shuffle_solvable_set_has_no_neighbours() -> None:
    tracks = [("a1.mp3", "A"), ("b1.mp3", "B"), ("a2.mp3", "A"), ("b2.mp3", "B"), ("c1.mp3", "C")]
    result = smart_shuffle(tracks)
    artists = {name: artist for name, artist in tracks}
    for prev, cur in pairwise(result):
        assert artists[prev] != artists[cur]


# --- быстрая настройка = быстрая генерация (финальный фидбек владельца) -------


def test_quick_setup_is_quick_generate(qtbot: QtBot, tmp_path: Path) -> None:
    """Без демо и превью; интро/аутро/первый трек на месте; кнопка «Сгенерировать»."""
    from PySide6.QtWidgets import QPushButton

    from modules.loopstudio.dialogs import QuickSetupDialog

    dialog = QuickSetupDialog(None, initial_video="C:/фон.mp4", initial_music=str(tmp_path))
    qtbot.addWidget(dialog)
    assert dialog.video_picker.path() == "C:/фон.mp4"
    assert dialog.music_picker.path() == str(tmp_path)
    assert dialog.intro_picker.path() == ""
    assert dialog.outro_picker.path() == ""
    texts = [b.text() for b in dialog.findChildren(QPushButton)]
    assert "Сгенерировать" in texts
    assert any(t.startswith("Пропустить") for t in texts)
    assert not any("демо" in t.lower() for t in texts)
    assert not any("предпросмотр" in t.lower() for t in texts)


def test_quick_setup_first_track_autofills_music_folder(qtbot: QtBot, tmp_path: Path) -> None:
    from modules.loopstudio.dialogs import QuickSetupDialog

    dialog = QuickSetupDialog(None)
    qtbot.addWidget(dialog)
    track = tmp_path / "01.mp3"
    track.write_bytes(b"x")
    dialog.first_track_picker.setPath(str(track))
    assert dialog.music_picker.path() == str(tmp_path)
