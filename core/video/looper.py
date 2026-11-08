"""Бесшовный видео-луп — этап D пайплайна и главное ноу-хау продукта.

Тяжёлое кодирование выполняется РОВНО ОДИН РАЗ: из исходника собирается
луп-сегмент (последние N секунд растворяются в первые через xfade) со всеми
эффектами. Дальше сегмент размножается concat demuxer'ом с ``-c copy`` —
без перекодирования, со скоростью копирования файла. Точная длина добирается
коротким «хвостом» (перекодируется только кусок короче одного сегмента).

Звуковых дорожек видеоряд не содержит: весь звук живёт в master-аудио
(звук исходного видео подмешивается туда отдельным слоем).
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from core.media.ffmpeg import ProgressCallback, run_ffmpeg
from core.media.probe import probe
from core.video.effects import scale_pad

_MIN_TAIL = 0.05  # хвосты короче 50 мс не собираем — погрешность округления


class LooperError(RuntimeError):
    """Невозможно собрать видеоряд (некорректные длительности, битые части)."""


class DiskSpaceError(RuntimeError):
    """Мест на диске не хватит — честно останавливаемся ДО многочасовой работы."""


def _gb(size_bytes: float) -> str:
    return f"{size_bytes / 1e9:.1f} ГБ"


def ensure_free_space(folder: Path, needed_bytes: float, *, what: str) -> None:
    """Проверить свободное место с понятным сообщением об ошибке."""
    free = shutil.disk_usage(folder).free
    if free < needed_bytes:
        drive = folder.drive or str(folder)
        raise DiskSpaceError(
            f"Недостаточно места на диске {drive} для {what}: нужно ещё примерно "
            f"{_gb(needed_bytes)}, свободно {_gb(free)}. Освободите место "
            f"или выберите другую папку сохранения (шаг 7)."
        )


@dataclass(frozen=True, slots=True)
class VideoEncodeSettings:
    """Параметры кодирования всех видео-частей (сегмент, интро, аутро, хвост).

    Все части кодируются ОДИНАКОВО — обязательное условие склейки без
    перекодирования (concat demuxer + ``-c copy``).

    ``codec``: libx264 (CPU) либо аппаратный h264_nvenc / h264_qsv / h264_amf —
    выбирается автодетектом в пайплайне; ``crf`` для аппаратных кодеров
    переводится в их эквивалент постоянного качества (cq / global_quality / qp).
    """

    width: int
    height: int
    fps: float = 30.0
    codec: str = "libx264"
    preset: str = "medium"
    crf: int = 18

    def _quality_args(self) -> list[str]:
        # аппаратные кодеры при том же числе «качества» пишут заметно больший
        # битрейт, чем x264 CRF — сдвигаем шкалу, чтобы размер файла был
        # сопоставим с x264 при том же визуальном качестве
        if self.codec == "h264_nvenc":
            cq = min(51, self.crf + 5)
            return ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
        if self.codec == "h264_qsv":
            return ["-preset", "medium", "-global_quality", str(min(51, self.crf + 3))]
        if self.codec == "h264_amf":
            qp = str(min(51, self.crf + 3))
            return ["-quality", "quality", "-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
        return ["-preset", self.preset, "-crf", str(self.crf)]

    def encode_args(self) -> list[str]:
        return [
            "-c:v",
            self.codec,
            *self._quality_args(),
            "-pix_fmt",
            "yuv420p",
            "-video_track_timescale",
            "90000",
            "-an",
        ]


@dataclass(frozen=True, slots=True)
class OverlaySpec:
    """VFX-оверлей поверх лупа: файл, непрозрачность, режим наложения."""

    path: Path
    opacity: float = 0.5
    screen_blend: bool = True  # True: screen-blend (mp4), False: alpha (webm с альфой)


def _effective_crossfade(duration: float, crossfade: float) -> float:
    """Кроссфейд не может съесть весь исходник: ограничиваем безопасно."""
    return min(crossfade, max(0.3, (duration - 0.5) / 2))


def make_loop_segment(
    source: Path,
    output: Path,
    settings: VideoEncodeSettings,
    *,
    seamless: bool = True,
    crossfade: float = 1.0,
    post_vf: str | None = None,
    overlay: OverlaySpec | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> float:
    """Собрать и закодировать луп-сегмент. Возвращает его длительность (сек).

    Эффекты (``post_vf``) и оверлей применяются после сборки лупа, поэтому
    xfade-переход обрабатывается вместе с телом и остаётся бесшовным.
    """
    info = probe(source)
    if not info.has_video:
        raise LooperError(f"В файле «{source.name}» нет видеопотока.")
    duration = info.duration
    base = scale_pad(settings.width, settings.height, settings.fps)

    lines: list[str] = []
    if seamless:
        xfade = _effective_crossfade(duration, crossfade)
        segment_duration = duration - xfade
        lines += [
            f"[0:v]{base},split=3[sb][st][sh]",
            f"[sb]trim={xfade:.3f}:{duration - xfade:.3f},setpts=PTS-STARTPTS[body]",
            f"[st]trim=start={duration - xfade:.3f},setpts=PTS-STARTPTS[tail]",
            f"[sh]trim=0:{xfade:.3f},setpts=PTS-STARTPTS[head]",
            f"[tail][head]xfade=transition=fade:duration={xfade:.3f}:offset=0[xf]",
            "[body][xf]concat=n=2:v=1:a=0[loop]",
        ]
    else:
        segment_duration = duration
        lines.append(f"[0:v]{base}[loop]")

    label = "loop"
    if post_vf:
        lines.append(f"[{label}]{post_vf}[fx]")
        label = "fx"

    args: list[str] = ["-i", str(source)]
    if overlay is not None:
        args += ["-stream_loop", "-1", "-i", str(overlay.path)]
        opacity = min(max(overlay.opacity, 0.0), 1.0)
        if overlay.screen_blend:
            lines.append(
                f"[1:v]scale={settings.width}:{settings.height},setsar=1,fps={settings.fps:g},"
                f"trim=0:{segment_duration:.3f},setpts=PTS-STARTPTS,format=yuv420p[ovl]"
            )
            lines.append(
                f"[{label}][ovl]blend=all_mode=screen:all_opacity={opacity:.3f},format=yuv420p[v]"
            )
        else:
            lines.append(
                f"[1:v]scale={settings.width}:{settings.height},setsar=1,fps={settings.fps:g},"
                f"trim=0:{segment_duration:.3f},setpts=PTS-STARTPTS,"
                f"format=rgba,colorchannelmixer=aa={opacity:.3f}[ovl]"
            )
            lines.append(f"[{label}][ovl]overlay=0:0,format=yuv420p[v]")
        label = "v"

    args += [
        "-filter_complex",
        ";".join(lines),
        "-map",
        f"[{label}]",
        *settings.encode_args(),
        str(output),
    ]
    run_ffmpeg(args, on_progress=on_progress, cancel=cancel)
    return probe(output).duration


def conform_clip(
    source: Path,
    output: Path,
    settings: VideoEncodeSettings,
    *,
    fade_in: float = 0.0,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> float:
    """Перекодировать интро/аутро в параметры сегмента (условие склейки copy).

    :param fade_in: «плавный старт» — появление из чёрного, сек (0 = выкл).
    """
    vf = scale_pad(settings.width, settings.height, settings.fps)
    if fade_in > 0:
        vf += f",fade=t=in:st=0:d={fade_in:.3f}"
    run_ffmpeg(
        ["-i", str(source), "-vf", vf, *settings.encode_args(), str(output)],
        on_progress=on_progress,
        cancel=cancel,
    )
    return probe(output).duration


def _fade_in_copy(
    segment: Path,
    output: Path,
    settings: VideoEncodeSettings,
    fade_in: float,
    *,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Fade-версия сегмента для «плавного старта» без интро.

    На луп-сегмент фейд вешать нельзя — concat copy размножил бы его в каждом
    повторе; поэтому из чёрного появляется только ПЕРВАЯ копия (отдельный
    перекодированный экземпляр с теми же параметрами — склейка copy валидна).
    """
    run_ffmpeg(
        [
            "-i",
            str(segment),
            "-vf",
            f"fade=t=in:st=0:d={fade_in:.3f}",
            *settings.encode_args(),
            str(output),
        ],
        on_progress=on_progress,
        cancel=cancel,
    )


def _make_tail(
    segment: Path,
    output: Path,
    settings: VideoEncodeSettings,
    duration: float,
    *,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Хвост точной длины: перекодируется только кусок короче одного сегмента."""
    run_ffmpeg(
        ["-i", str(segment), "-t", f"{duration:.3f}", *settings.encode_args(), str(output)],
        on_progress=on_progress,
        cancel=cancel,
    )


def concat_files(
    parts: list[Path],
    output: Path,
    list_file: Path,
    *,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Склейка частей БЕЗ перекодирования (concat demuxer + ``-c copy``)."""
    escaped = [str(p).replace("'", r"'\''") for p in parts]
    list_file.write_text("".join(f"file '{e}'\n" for e in escaped), encoding="utf-8")
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)],
        on_progress=on_progress,
        cancel=cancel,
    )


def build_looped_video(
    source: Path,
    target_duration: float,
    output: Path,
    settings: VideoEncodeSettings,
    *,
    work_dir: Path,
    seamless: bool = True,
    crossfade: float = 1.0,
    post_vf: str | None = None,
    overlay: OverlaySpec | None = None,
    intro: Path | None = None,
    outro: Path | None = None,
    start_fade: float = 0.0,
    extra_reserved_bytes: float = 0.0,
    on_progress: ProgressCallback | None = None,
    on_concat_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Собрать видеоряд ровно ``target_duration`` секунд: интро + луп + хвост + аутро.

    :param start_fade: «плавный старт» — появление из чёрного, сек: при интро
        фейд получает интро, иначе — отдельная fade-копия первого сегмента.
    :param extra_reserved_bytes: сколько места на том же диске уже занято другими
        артефактами рендера (master-аудио) — учитывается в проверке места.
    :param on_concat_progress: отдельный колбэк финальной склейки (секунды
        выходного видеоряда) — этап долгий на многочасовых роликах.
    """
    intro_part: Path | None = None
    outro_part: Path | None = None
    intro_duration = outro_duration = 0.0
    if intro is not None:
        intro_part = work_dir / "intro.mp4"
        intro_duration = conform_clip(
            intro, intro_part, settings, fade_in=start_fade, cancel=cancel
        )
    if outro is not None:
        outro_part = work_dir / "outro.mp4"
        outro_duration = conform_clip(outro, outro_part, settings, cancel=cancel)

    body_target = target_duration - intro_duration - outro_duration
    if body_target <= 0:
        raise LooperError(
            f"Интро ({intro_duration:.1f} с) и аутро ({outro_duration:.1f} с) длиннее "
            f"всего ролика ({target_duration:.1f} с) — увеличьте длительность."
        )

    segment = work_dir / "segment.mp4"
    segment_duration = make_loop_segment(
        source,
        segment,
        settings,
        seamless=seamless,
        crossfade=crossfade,
        post_vf=post_vf,
        overlay=overlay,
        on_progress=on_progress,
        cancel=cancel,
    )
    if segment_duration <= 0:
        raise LooperError("Луп-сегмент получился нулевой длины — исходник повреждён?")

    full_repeats = int(body_target // segment_duration)
    tail_duration = body_target - full_repeats * segment_duration

    # точная оценка: видеоряд ≈ сегмент × повторы (+ fade-копия при плавном
    # старте без интро); +8 % запас на контейнер
    extra_copies = 1 if (start_fade > 0 and intro_part is None and full_repeats > 0) else 0
    projected = segment.stat().st_size * (
        full_repeats + extra_copies + (1 if tail_duration > 0 else 0)
    )
    ensure_free_space(work_dir, projected * 1.08 + extra_reserved_bytes, what="сборки видеоряда")

    parts: list[Path] = []
    if intro_part is not None:
        parts.append(intro_part)
    body_parts = [segment] * full_repeats
    if start_fade > 0 and intro_part is None and full_repeats > 0:
        # плавный старт без интро: первая копия сегмента появляется из чёрного
        faded_first = work_dir / "segment_fade_in.mp4"
        _fade_in_copy(
            segment, faded_first, settings, start_fade, on_progress=on_progress, cancel=cancel
        )
        body_parts[0] = faded_first
    parts += body_parts
    if tail_duration >= _MIN_TAIL:
        tail = work_dir / "tail.mp4"
        _make_tail(segment, tail, settings, tail_duration, cancel=cancel)
        if start_fade > 0 and intro_part is None and full_repeats == 0:
            # ролик короче одного сегмента: фейд достаётся хвосту
            faded_tail = work_dir / "tail_fade_in.mp4"
            _fade_in_copy(tail, faded_tail, settings, start_fade, cancel=cancel)
            tail = faded_tail
        parts.append(tail)
    if outro_part is not None:
        parts.append(outro_part)
    if not parts:
        raise LooperError("Видеоряд пуст: нечего склеивать.")

    concat_files(
        parts, output, work_dir / "concat_list.txt", on_progress=on_concat_progress, cancel=cancel
    )
    return output
