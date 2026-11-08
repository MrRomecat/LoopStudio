"""Паспорт рендера: план/факт, швы лупа, кодер, предупреждения, настройки."""

from __future__ import annotations

from pathlib import Path

from core.audio.timeline import build_timeline
from core.render.job import AmbientConfig, EffectsConfig, RenderJob
from core.render.passport import build_passport_text
from core.render.verify import VerifyReport


def _passport(tmp_path: Path, **overrides: object) -> str:
    timeline = build_timeline([(Path("01 Дождь.wav"), 70.0), (Path("02 Ночь.wav"), 50.0)], 120.0)
    output = tmp_path / "Ролик.mp4"
    output.write_bytes(b"0" * 1024)
    job = RenderJob(
        video_path="C:/видео/море.mp4",
        intro_path="C:/видео/интро.mp4",
        music_dir="C:/музыка/пак1",
        duration_sec=120.0,
        source_audio=True,
        source_audio_volume=0.5,
        normalize=True,
        ambient=[AmbientConfig(enabled=True, path="C:/звуки/дождь.flac", volume=0.4)],
        effects=EffectsConfig(seamless_loop=True, ken_burns=True, vignette=True),
        resolution="2160p",
        quality="high",
    )
    kwargs: dict[str, object] = {
        "job": job,
        "timeline": timeline,
        "output_path": output,
        "calculated_duration": 120.0,
        "codec": "h264_nvenc",
        "elapsed_sec": 630.0,
        "warnings": [],
        "verify": VerifyReport(
            ok=True, checks=("Видео- и аудиопоток на месте",), problems=(), file_duration=120.2
        ),
        "loop_period": 30.0,
        "intro_duration": 10.0,
    }
    kwargs.update(overrides)
    return build_passport_text(**kwargs)  # type: ignore[arg-type]


def test_passport_contains_key_facts(tmp_path: Path) -> None:
    text = _passport(tmp_path)
    assert "паспорт рендера" in text
    assert "Задано: 0:02:00" in text
    assert "Расчёт (резиновый таймлайн" in text
    assert "Файл по факту: 0:02:00" in text
    assert "Уникальных треков: 2" in text
    assert "видеокарта NVIDIA (NVENC)" in text
    # швы: интро 10 c + период 30 c → 0:00:40, 0:01:10, 0:01:40
    assert "первые швы: 0:00:40, 0:01:10, 0:01:40" in text
    assert "ПРОВЕРЕНО" in text
    assert "— Предупреждения —\nнет" in text
    # блок точного переиздания
    assert "C:/музыка/пак1" in text
    assert "дождь.flac" in text and "40%" in text
    assert "3840×2160" in text
    assert "время рендера: 10:30" in text


def test_passport_lists_warnings_and_problems(tmp_path: Path) -> None:
    text = _passport(
        tmp_path,
        warnings=["Ролик сохранён без «быстрого старта»"],
        verify=VerifyReport(
            ok=False, checks=(), problems=("хвост файла не декодируется",), file_duration=None
        ),
    )
    assert "• Ролик сохранён без «быстрого старта»" in text
    assert "✗ хвост файла не декодируется" in text
    assert "НЕ ПРОЙДЕНА" in text
