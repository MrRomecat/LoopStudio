"""Этап 5: экран собирается, биндинг RenderJob ↔ UI работает в обе стороны."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from core.render.job import AmbientConfig, EffectsConfig, RenderJob
from core.settings import AppSettings
from modules.loopstudio.screen import LoopStudioScreen


def _screen(qtbot: QtBot) -> LoopStudioScreen:
    screen = LoopStudioScreen(AppSettings())
    qtbot.addWidget(screen)
    return screen


def test_default_state(qtbot: QtBot) -> None:
    screen = _screen(qtbot)
    job = screen.collect_job()
    assert job.duration_sec == 3600.0
    assert job.resolution == "1080p"
    assert job.quality == "high"
    assert job.effects.seamless_loop is True
    assert job.clean_metadata is True
    assert len(job.ambient) == 3
    assert not any(a.enabled for a in job.ambient)


def test_roundtrip_apply_collect(qtbot: QtBot, music_dir: Path, video_5s: Path) -> None:
    screen = _screen(qtbot)
    original = RenderJob(
        video_path=str(video_5s),
        source_audio=True,
        source_audio_volume=0.4,
        music_dir=str(music_dir),
        fade_out=2.5,
        pause=1.0,
        crossfade=3.0,
        normalize=True,
        music_volume=0.8,
        ambient=[
            AmbientConfig(enabled=True, path=str(music_dir / "01.wav"), volume=0.25),
            AmbientConfig(),
            AmbientConfig(),
        ],
        effects=EffectsConfig(
            seamless_loop=False,
            loop_crossfade=2.0,
            ken_burns=True,
            lut="night",
            vignette=True,
            vignette_strength=0.6,
            film_grain=True,
            film_grain_strength=12,
        ),
        resolution="2160p",
        quality="standard",
        duration_sec=7200.0,
        output_dir="C:/out",
        title_template="Ночь {date} {duration}",
        clean_metadata=False,
    )
    screen.apply_job(original)
    collected = screen.collect_job()

    assert collected.video_path == original.video_path
    assert collected.source_audio is True
    assert collected.source_audio_volume == original.source_audio_volume
    assert collected.fade_out == original.fade_out
    assert collected.pause == original.pause
    assert collected.crossfade == original.crossfade
    assert collected.normalize is True
    assert collected.music_volume == original.music_volume
    assert collected.ambient[0] == original.ambient[0]
    assert collected.effects == original.effects
    assert collected.resolution == original.resolution
    assert collected.quality == original.quality
    assert collected.duration_sec == original.duration_sec
    assert collected.output_dir == original.output_dir
    assert collected.title_template == original.title_template
    assert collected.clean_metadata is False
    # выбор папки с музыкой заполнил список треков в порядке сортировки
    assert collected.track_order == ["01.wav", "02.wav", "03.wav"]


def test_duration_slider_syncs_time_field(qtbot: QtBot) -> None:
    screen = _screen(qtbot)
    screen.duration_slider.setValue(90)
    assert screen.duration_edit.time().hour() == 1
    assert screen.duration_edit.time().minute() == 30
    job = screen.collect_job()
    assert job.duration_sec == 5400.0


def test_wizard_pages_and_navigation(qtbot: QtBot) -> None:
    """Мастер «Vinyl Warm»: 6 страниц, свободная навигация, футер честный."""
    screen = _screen(qtbot)
    assert len(screen.pages) == 6
    assert screen.current_step() == 0
    assert not screen.back_btn.isVisibleTo(screen)
    screen.go_to_step(5)
    assert screen.current_step() == 5
    assert not screen.continue_btn.isVisibleTo(screen)  # на «Рендере» некуда продолжать
    screen.go_to_step(99)  # выход за границы безопасно клампится
    assert screen.current_step() == 5
    screen.go_to_step(4)
    assert screen.continue_btn.text() == "К генерации ›"


def test_info_dots_and_batch_state(qtbot: QtBot) -> None:
    """Стандарт ⓘ: подсказки есть во всех шагах; пакетный режим — только выбор."""
    from core.ui.widgets import InfoDot

    screen = _screen(qtbot)
    dots = screen.findChildren(InfoDot)
    assert len(dots) >= 20, f"значков ⓘ слишком мало: {len(dots)}"
    assert all(d.toolTip() for d in dots)

    assert screen.batch_clear_btn.isHidden()
    screen.set_batch_state(3)
    assert "3 папок" in screen.batch_btn.text()
    assert not screen.music_picker.isEnabled()
    screen.set_batch_state(None)
    assert screen.batch_btn.text() == "Несколько папок…"
    assert screen.music_picker.isEnabled()
    # стоп-кнопка — читаемый квадрат, крестик очистки — «×»
    assert screen.stop_mix_btn.text() == "■"


def test_wave3_widgets_and_roundtrip(qtbot: QtBot, music_dir: Path) -> None:
    """Волна 3: плавный старт, трим, точная нормализация, исключение треков."""
    screen = _screen(qtbot)
    assert screen.shuffle_btn.text() == "⇄ Перемешать"
    assert screen.lint_btn.text() == "Анализ громкости"
    assert not screen.smooth_start_sec.isEnabled()
    screen.smooth_start_toggle.setChecked(True)
    assert screen.smooth_start_sec.isEnabled()
    assert not screen.normalize_precise_toggle.isEnabled()
    screen.normalize_toggle.setChecked(True)
    assert screen.normalize_precise_toggle.isEnabled()

    original = RenderJob(
        music_dir=str(music_dir),
        track_order=["02.wav", "01.wav", "03.wav"],
        excluded_tracks=["01.wav"],
        trim_silence=True,
        normalize=True,
        normalize_precise=True,
        smooth_start=True,
        smooth_start_sec=2.5,
    )
    screen.apply_job(original)
    collected = screen.collect_job()
    assert collected.track_order == ["02.wav", "01.wav", "03.wav"]
    assert collected.excluded_tracks == ["01.wav"]
    assert collected.trim_silence is True
    assert collected.normalize_precise is True
    assert collected.smooth_start is True
    assert collected.smooth_start_sec == 2.5
    assert "активно 2 из 3" in screen.track_counter.text()


def test_wave2_widgets_and_package_roundtrip(qtbot: QtBot) -> None:
    """Волна 2: профили, пакетный режим и тумблер пакет-папки на месте."""
    screen = _screen(qtbot)
    assert screen.profile_combo.count() == 0
    assert screen.batch_btn.text() == "Несколько папок…"
    assert screen.package_toggle.isChecked()
    assert screen.collect_job().package_folder is True
    screen.package_toggle.setChecked(False)
    assert screen.collect_job().package_folder is False
    screen.apply_job(RenderJob(package_folder=True))
    assert screen.package_toggle.isChecked()


def test_volume_sliders_follow_toggles(qtbot: QtBot) -> None:
    """Громкость активна только при включённом тумблере — иначе «звук выключен»."""
    screen = _screen(qtbot)
    assert not screen.source_audio_volume.isEnabled()
    screen.source_audio_toggle.setChecked(True)
    assert screen.source_audio_volume.isEnabled()
    screen.source_audio_toggle.setChecked(False)
    assert not screen.source_audio_volume.isEnabled()

    row = screen.ambient_rows[0]
    assert not row.volume.isEnabled()
    row.toggle.setChecked(True)
    assert row.volume.isEnabled()


def test_quick_setup_dialog_durations(qtbot: QtBot) -> None:
    """Быстрая настройка: весь ряд длительностей до 10 часов, выбор фиксируется."""
    from modules.loopstudio.dialogs import QuickSetupDialog

    dialog = QuickSetupDialog(None)
    qtbot.addWidget(dialog)
    minutes = [m for _label, m in dialog._durations]
    assert minutes == [30, 60, 120, 180, 300, 480, 600]
    # по умолчанию выбран «1 час»
    assert dialog.duration_minutes == 60
    assert [b.isChecked() for b in dialog._duration_buttons] == [m == 60 for m in minutes]
    # клик по «10 часов» переносит и выбор, и подсветку
    dialog._duration_buttons[-1].click()
    assert dialog.duration_minutes == 600
    assert [b.isChecked() for b in dialog._duration_buttons] == [m == 600 for m in minutes]


def test_quick_setup_button_and_checked_style(qtbot: QtBot) -> None:
    """Кнопка «Быстрая настройка» вверху формы; тема подсвечивает checked-кнопки."""
    from core.ui import theme

    screen = _screen(qtbot)
    assert screen.quick_setup_btn.text() == "Быстрая настройка"
    assert screen.quick_setup_btn.toolTip()
    with qtbot.waitSignal(screen.quick_setup_clicked, timeout=1000):
        screen.quick_setup_btn.click()
    # регресс бага: выбранная (checked) кнопка обязана отличаться визуально
    assert "QPushButton:checked" in theme.STYLESHEET
