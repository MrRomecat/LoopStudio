"""Этап 4 (критерий): полный 2-мин ролик из фикстур; метаданных источника нет в выходе."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from core.audio.timeline import build_timeline
from core.media.ffmpeg import FFmpegCancelled, find_ffprobe
from core.media.probe import probe, probe_chapters
from core.render.job import AmbientConfig, EffectsConfig, RenderJob
from core.render.pipeline import render_job
from core.render.progress import ProgressUpdate
from core.render.tracklist import build_ffmetadata, build_tracklist_text


def _all_tags_text(path: Path) -> str:
    """Весь вывод ffprobe (формат+потоки+главы) для поиска следов источника."""
    result = subprocess.run(
        [
            str(find_ffprobe()),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    return result.stdout


TRACKLIST_TRACKS = [(Path("Дождь.wav"), 65.0), (Path("02.wav"), 70.0)]


class TestTracklist:
    def test_text_format(self) -> None:
        tl = build_timeline(TRACKLIST_TRACKS, 130.0)
        text = build_tracklist_text(tl)
        assert text.splitlines() == ["00:00:00 Дождь", "00:01:05 02"]

    def test_ffmetadata_chapters(self) -> None:
        tl = build_timeline(TRACKLIST_TRACKS, 130.0)
        meta = build_ffmetadata(tl, title="Мой ролик = тест; #1")
        assert meta.startswith(";FFMETADATA1")
        assert "title=Мой ролик \\= тест\\; \\#1" in meta
        assert meta.count("[CHAPTER]") == 2
        assert "START=65000" in meta and "END=135000" in meta


class TestFullRender:
    def test_two_minute_video(
        self, video_with_metadata: Path, music_dir: Path, tmp_path: Path
    ) -> None:
        out_dir = tmp_path / "выход"
        job = RenderJob(
            video_path=str(video_with_metadata),
            music_dir=str(music_dir),
            output_dir=str(out_dir),
            duration_sec=120.0,
            resolution="720p",
            quality="standard",
            source_audio=True,
            source_audio_volume=0.3,
            normalize=False,
            fade_out=0.5,
            music_volume=0.9,
            ambient=[AmbientConfig(enabled=True, path=str(music_dir / "01.wav"), volume=0.2)],
            effects=EffectsConfig(seamless_loop=True, loop_crossfade=1.0, vignette=True),
            title_template="Тест {date} {duration}",
            clean_metadata=True,
        )
        updates: list[ProgressUpdate] = []
        result = render_job(job, on_progress=updates.append)

        # пакет-папка: всё для одного ролика лежит в своей подпапке
        assert result.output_path.is_file()
        package = result.output_path.parent
        assert package.parent == out_dir
        assert package.name == result.output_path.stem
        assert result.tracklist_path.is_file()
        assert result.tracklist_path.parent == package
        first_line = result.tracklist_path.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "00:00:00 01"

        # автокопия проекта: переиздание без реконструкции по памяти
        sidecar = result.output_path.with_suffix(".lsproj")
        assert sidecar.is_file()
        reloaded = RenderJob.load(sidecar)
        assert reloaded.music_dir == str(music_dir)
        assert reloaded.duration_sec == 120.0

        # кредиты: у фикстур тегов нет — все треки в секции «заполните вручную»
        credits = result.output_path.with_name(f"{result.output_path.stem} — кредиты.txt")
        assert credits.is_file()
        credits_text = credits.read_text(encoding="utf-8")
        assert "не заполнен тег исполнителя" in credits_text
        assert "01.wav" in credits_text

        # паспорт рендера: написан, автопроверка пройдена, предупреждений нет
        assert result.passport_path is not None and result.passport_path.is_file()
        passport = result.passport_path.read_text(encoding="utf-8")
        assert "ПРОВЕРЕНО — файл целый" in passport
        assert "Уникальных треков: 3" in passport
        assert "— Предупреждения —\nнет" in passport
        assert result.warnings == ()

        # длительность точная (резиновый таймлайн: 120 c ровно из 30 треков)
        info = probe(result.output_path)
        assert result.duration == pytest.approx(120.0)
        assert info.duration == pytest.approx(120.0, abs=0.2)
        assert info.has_video and info.has_audio
        assert (info.width, info.height) == (1280, 720)
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"

        # главы соответствуют трекам
        chapters = probe_chapters(result.output_path)
        assert len(chapters) == len(result.timeline.entries) == 30
        assert chapters[0].title == "01"
        assert chapters[1].start == pytest.approx(3.0, abs=0.05)

        # метаданных источника нет; нейтральные подставлены
        tags_text = _all_tags_text(result.output_path)
        assert "Secret" not in tags_text
        data = json.loads(tags_text)
        assert data["format"]["tags"]["title"].startswith("Тест ")
        assert "2ч" not in data["format"]["tags"]["title"]  # 120 c = 2м, не 2ч
        assert "2м" in data["format"]["tags"]["title"]

        # прогресс: монотонный, дошёл до 100%, ETA появлялся
        fractions = [u.fraction for u in updates]
        assert fractions == sorted(fractions)
        assert fractions[-1] == pytest.approx(1.0)
        assert any(u.eta_seconds is not None for u in updates)
        assert updates[-1].stage_index == updates[-1].stage_count

    def test_flat_output_and_folder_placeholder(
        self, video_5s: Path, music_dir: Path, tmp_path: Path
    ) -> None:
        """package_folder=False кладёт файлы прямо в папку; {folder} = имя папки музыки."""
        out_dir = tmp_path / "плоско"
        job = RenderJob(
            video_path=str(video_5s),
            music_dir=str(music_dir),
            output_dir=str(out_dir),
            duration_sec=60.0,
            resolution="720p",
            quality="standard",
            package_folder=False,
            title_template="Тест {folder}",
        )
        result = render_job(job)
        assert result.output_path.parent == out_dir
        assert result.output_path.stem == "Тест music"  # имя папки фикстур музыки
        assert result.output_path.with_suffix(".lsproj").is_file()

    def test_ffmpeg_failure_writes_log(
        self, video_5s: Path, music_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сбой ffmpeg оставляет лог в папке сохранения, путь виден в ошибке."""
        import core.render.pipeline as pipeline_module
        from core.media.ffmpeg import FFmpegError

        def boom(*args: object, **kwargs: object) -> None:
            raise FFmpegError(
                "ffmpeg завершился с ошибкой (код -22). Подробности — в логе.",
                stderr_tail="Invalid argument: тест-хвост",
                command="ffmpeg -i тест.wav",
            )

        monkeypatch.setattr(pipeline_module, "build_master", boom)
        out_dir = tmp_path / "выход"
        job = RenderJob(
            video_path=str(video_5s),
            music_dir=str(music_dir),
            output_dir=str(out_dir),
            duration_sec=60.0,
        )
        with pytest.raises(FFmpegError) as exc_info:
            render_job(job)
        logs = list(out_dir.glob("LoopStudio — лог ошибки *.txt"))
        assert len(logs) == 1
        content = logs[0].read_text(encoding="utf-8")
        assert "Invalid argument: тест-хвост" in content
        assert "ffmpeg -i тест.wav" in content
        assert str(logs[0]) in str(exc_info.value)

    def test_cancel_interrupts(self, video_5s: Path, music_dir: Path, tmp_path: Path) -> None:
        job = RenderJob(
            video_path=str(video_5s),
            music_dir=str(music_dir),
            output_dir=str(tmp_path / "out"),
            duration_sec=600.0,
        )
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(FFmpegCancelled):
            render_job(job, cancel=cancel)
        assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
