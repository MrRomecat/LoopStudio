"""Паспорт рендера — «<имя> — паспорт.txt» рядом с готовым роликом.

Отвечает на утренние вопросы серийщика без открытия плеера: какая вышла
длительность, все ли треки вошли, где швы видео-лупа, чем кодировалось,
какие были предупреждения и прошёл ли файл автопроверку. Заодно фиксирует
все настройки задания — точное переиздание без реконструкции по памяти.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from core.audio.timeline import Timeline
from core.render.job import RESOLUTIONS, RenderJob
from core.render.verify import VerifyReport

_LUT_NAMES = {
    "warm": "Тёплый",
    "cold": "Холодный",
    "night": "Ночной",
    "vhs": "VHS",
    "sepia": "Сепия",
}
_QUALITY_NAMES = {"source": "Как источник", "high": "Высокое", "standard": "Стандарт"}
_CODEC_NAMES = {
    "libx264": "процессор (x264)",
    "h264_nvenc": "видеокарта NVIDIA (NVENC)",
    "h264_qsv": "видеокарта Intel (QSV)",
    "h264_amf": "видеокарта AMD (AMF)",
}


def _hms(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _size_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "неизвестен"
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} ГБ"
    return f"{size / 1024**2:.0f} МБ"


def _percent(fraction: float) -> str:
    return f"{round(fraction * 100)}%"


def _effects_lines(job: RenderJob) -> list[str]:
    fx = job.effects
    lines: list[str] = []
    if fx.seamless_loop:
        lines.append(f"бесшовный луп (кроссфейд {fx.loop_crossfade:g} с)")
    if fx.ken_burns:
        lines.append("Кен Бёрнс (дыхание камеры)")
    if fx.lut:
        lines.append(f"цветовой фильтр «{_LUT_NAMES.get(fx.lut, fx.lut)}»")
    if fx.overlay_path:
        mode = "screen" if fx.overlay_screen_blend else "альфа"
        lines.append(
            f"оверлей {Path(fx.overlay_path).name} ({_percent(fx.overlay_opacity)}, режим {mode})"
        )
    if fx.vignette:
        lines.append(f"виньетка {_percent(fx.vignette_strength)}")
    if fx.film_grain:
        lines.append(f"плёночное зерно {fx.film_grain_strength}")
    return lines or ["нет"]


def _job_settings_block(job: RenderJob) -> list[str]:
    lines = [f"Основное видео: {job.video_path}"]
    if job.intro_path:
        lines.append(f"Интро: {job.intro_path}")
    if job.outro_path:
        lines.append(f"Аутро: {job.outro_path}")
    if job.source_audio:
        lines.append(f"Оригинальный звук видео: {_percent(job.source_audio_volume)}")
    lines.append(f"Папка музыки: {job.music_dir}")
    order = "ручной порядок" if job.track_order else "автосортировка"
    lines.append(f"Порядок треков: {order}")
    if job.excluded_tracks:
        lines.append(f"Исключено треков: {len(job.excluded_tracks)}")
    normalize_text = "выкл"
    if job.normalize:
        normalize_text = "вкл (точная, 2 прохода)" if job.normalize_precise else "вкл"
    lines.append(
        f"Fade-out: {job.fade_out:g} с · пауза: {job.pause:g} с · "
        f"кроссфейд: {job.crossfade:g} с · нормализация −14 LUFS: {normalize_text}"
    )
    if job.trim_silence:
        lines.append("Обрезка тишины на краях треков: вкл")
    if job.smooth_start:
        lines.append(f"Плавный старт (видео из чёрного + fade-in): {job.smooth_start_sec:g} с")
    lines.append(f"Громкость музыки: {_percent(job.music_volume)}")
    for i, layer in enumerate(job.ambient, start=1):
        if layer.enabled and layer.path:
            lines.append(
                f"Эмбиент-слой {i}: {Path(layer.path).name} — {_percent(layer.volume)} "
                f"({layer.path})"
            )
    lines.append("Эффекты: " + "; ".join(_effects_lines(job)))
    width, height = RESOLUTIONS[job.resolution]
    lines.append(
        f"Качество: {width}×{height} · режим «{_QUALITY_NAMES.get(job.quality, job.quality)}»"
    )
    lines.append(f"Задание длительности: {_hms(job.duration_sec)}")
    lines.append(f"Шаблон названия: {job.title_template}")
    lines.append(f"Очистка метаданных: {'вкл' if job.clean_metadata else 'выкл'}")
    return lines


def build_passport_text(
    *,
    job: RenderJob,
    timeline: Timeline,
    output_path: Path,
    calculated_duration: float,
    codec: str,
    elapsed_sec: float,
    warnings: Sequence[str],
    verify: VerifyReport | None,
    loop_period: float | None,
    intro_duration: float = 0.0,
) -> str:
    """Собрать текст паспорта. Чистая функция: пишет вызывающий."""
    unique_tracks = len({entry.path for entry in timeline.entries})
    positions = len(timeline.entries)
    rounds = positions // unique_tracks if unique_tracks else 0

    out: list[str] = [
        "LoopStudio — паспорт рендера",
        f"Файл: {output_path.name}",
        f"Готов: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Размер: {_size_text(output_path)} · время рендера: {_mmss(elapsed_sec)}",
        "",
        "— Длительность —",
        f"Задано: {_hms(job.duration_sec)}",
        f"Расчёт (резиновый таймлайн, музыка не обрезается): {_hms(calculated_duration)}",
    ]
    if verify is not None and verify.file_duration is not None:
        out.append(f"Файл по факту: {_hms(verify.file_duration)}")
    out += [
        "",
        "— Музыка —",
        f"Уникальных треков: {unique_tracks} · позиций в ролике: {positions}"
        + (f" (~{rounds} круга плейлиста)" if rounds > 1 else ""),
        "Таймкоды всех стыков — в файле «… — трек-лист.txt» и в главах mp4.",
        "",
        "— Видео —",
    ]
    width, height = RESOLUTIONS[job.resolution]
    out.append(
        f"Разрешение: {width}×{height} · режим «{_QUALITY_NAMES.get(job.quality, job.quality)}» "
        f"· кодер: {_CODEC_NAMES.get(codec, codec)}"
    )
    if loop_period is not None and loop_period > 0 and loop_period < calculated_duration:
        seams: list[str] = []
        seam = intro_duration + loop_period
        while seam < calculated_duration and len(seams) < 3:
            seams.append(_hms(seam))
            seam += loop_period
        if seams:
            out.append(
                f"Период видео-лупа: {_hms(loop_period)} · первые швы: {', '.join(seams)}"
                + (" (с учётом интро)" if intro_duration > 0 else "")
            )

    out += ["", "— Проверка файла —"]
    if verify is None:
        out.append("Проверка не выполнялась.")
    else:
        out.extend(f"✓ {line}" for line in verify.checks)
        out.extend(f"✗ {line}" for line in verify.problems)
        out.append(f"Итог: {verify.summary}")

    out += ["", "— Предупреждения —"]
    if warnings:
        out.extend(f"• {line}" for line in warnings)
    else:
        out.append("нет")

    out += ["", "— Настройки задания (для точного переиздания) —"]
    out.extend(_job_settings_block(job))
    out.append("")
    return "\n".join(out)
