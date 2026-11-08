"""Оркестратор рендера — этапы A–G из технической архитектуры.

A probe → B резиновый таймлайн → C аудио-мастер (один проход) →
D видео-луп (одно кодирование + concat copy) → E+F mux с главами и
метаданными (один быстрый ремукс) → G треклист и перенос в папку сохранения.

Temp-файлы живут в системном temp и удаляются всегда (finally).
"""

from __future__ import annotations

import contextlib
import random
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.audio.analyze import TrackAnalysis, analyze_track
from core.audio.mixdown import AmbientLayer, MixdownSettings, build_master
from core.audio.playlist import scan_music_folder, sort_tracks
from core.audio.timeline import Timeline, build_timeline
from core.media.encoders import detect_hw_encoder
from core.media.ffmpeg import FFmpegCancelled, FFmpegError, ProgressCallback, run_ffmpeg
from core.media.probe import MediaInfo, probe
from core.render.credits import build_credits_text, unique_tracks
from core.render.job import QUALITY_PRESETS, RESOLUTIONS, JobError, RenderJob
from core.render.passport import build_passport_text
from core.render.progress import PipelineProgress, ProgressSink
from core.render.tracklist import build_ffmetadata, build_tracklist_text
from core.render.verify import VerifyError, VerifyReport, verify_output
from core.video.effects import compose, film_grain, ken_burns, lut, vignette
from core.video.looper import (
    OverlaySpec,
    VideoEncodeSettings,
    build_looped_video,
    ensure_free_space,
)

_PREVIEW_DURATION = 60.0
_PREVIEW_CRF = 28
_MAX_FPS = 60.0
#: мягкое вступление трека после паузы-тишины при включённом «плавном старте»
_PAUSE_FADE_IN = 1.0
#: оценка master.flac (48 кГц stereo 24 бит × сжатие FLAC) для проверки места
#: до аудио-мастера; согласована с core/render/preflight.py
_MASTER_BYTES_PER_SEC = 48_000 * 2 * 3 * 0.7

_STAGES: list[tuple[str, float]] = [
    ("Анализ файлов", 2.0),
    ("Аудио-мастер", 8.0),
    ("Кодирование видео-лупа", 50.0),
    ("Сборка видеоряда", 14.0),
    ("Соединение аудио и видео", 13.0),
    ("Финализация и сохранение", 13.0),
]


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Итог рендера: готовый файл, треклист, паспорт, фактическая длительность."""

    output_path: Path
    tracklist_path: Path
    duration: float
    timeline: Timeline
    warnings: tuple[str, ...] = ()  # некритичные предупреждения для пользователя
    passport_path: Path | None = None  # «… — паспорт.txt» (None у предпросмотра)


def _human_duration(seconds: float) -> str:
    total_minutes = round(seconds / 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}ч{minutes:02d}м"
    if hours:
        return f"{hours}ч"
    return f"{minutes}м"


#: имена, зарезервированные Windows: файл «con.mp4» не создать ни в одной папке
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip(" .")
    cleaned = cleaned[:120].rstrip(" .")  # запас до MAX_PATH в глубоких папках
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"LoopStudio {cleaned}"
    return cleaned or "LoopStudio"


def _render_title(job: RenderJob, duration: float, preview: bool) -> str:
    title = job.title_template.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
    title = title.replace("{duration}", _human_duration(duration))
    folder = Path(job.music_dir).name if job.music_dir else ""
    title = title.replace("{folder}", folder)
    title = title.strip() or "LoopStudio"
    return f"{title} (предпросмотр)" if preview else title


def _unique_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def _unique_dir(folder: Path, name: str) -> Path:
    """Свободное имя подпапки-пакета (« (2)» при совпадении — как у файлов)."""
    candidate = folder / name
    counter = 2
    while candidate.exists():
        candidate = folder / f"{name} ({counter})"
        counter += 1
    return candidate


def _ordered_tracks(job: RenderJob) -> list[Path]:
    tracks = scan_music_folder(Path(job.music_dir))
    if job.excluded_tracks:
        excluded = set(job.excluded_tracks)
        kept = [t for t in tracks if t.name not in excluded]
        if tracks and not kept:
            raise JobError("Все треки исключены — отметьте галочкой хотя бы один (шаг 2).")
        tracks = kept
    if not job.track_order:
        return tracks
    manual = {name: i for i, name in enumerate(job.track_order)}
    in_order = sorted([t for t in tracks if t.name in manual], key=lambda t: manual[t.name])
    rest = sort_tracks([t for t in tracks if t.name not in manual])
    return in_order + rest


def _build_post_vf(
    job: RenderJob, width: int, height: int, fps: float, segment: float
) -> str | None:
    fx = job.effects
    return compose(
        ken_burns(width, height, duration=segment, fps=fps) if fx.ken_burns else None,
        lut(fx.lut) if fx.lut else None,
        vignette(fx.vignette_strength) if fx.vignette else None,
        film_grain(fx.film_grain_strength) if fx.film_grain else None,
    )


def render_job(
    job: RenderJob,
    *,
    preview: bool = False,
    preview_from_start: bool = False,
    on_progress: ProgressSink | None = None,
    cancel: threading.Event | None = None,
) -> RenderResult:
    """Выполнить полный рендер задания. Блокирующий вызов — запускать в фоне.

    :param preview: режим «Предпросмотр 1 минута» — низкое качество,
        случайное смещение плейлиста, та же логика пайплайна.
    :param preview_from_start: детерминированное превью «Начало ролика» —
        без случайного смещения: первые 30 секунд решают удержание, их и смотрим.
    :raises JobError, TimelineError, LooperError, ProbeError, FFmpegError,
        FFmpegCancelled: все — с человекочитаемыми сообщениями.
    """
    job.validate()
    started = time.monotonic()
    progress = PipelineProgress(on_progress, _STAGES)
    temp_dir = Path(tempfile.mkdtemp(prefix="loopstudio_"))
    try:
        return _render(job, preview, preview_from_start, progress, temp_dir, cancel, started)
    except FFmpegError as exc:
        log_path = _write_error_log(job, exc)
        message = str(exc)
        if log_path is not None:
            message = message.replace("в логе.", f"в логе:\n{log_path}")
        raise FFmpegError(message, stderr_tail=exc.stderr_tail, command=exc.command) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _write_error_log(job: RenderJob, exc: FFmpegError) -> Path | None:
    """Сохранить подробности сбоя ffmpeg в папку сохранения (видна пользователю)."""
    try:
        folder = Path(job.output_dir) if job.output_dir else Path(tempfile.gettempdir())
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        log_path = folder / f"LoopStudio — лог ошибки {stamp}.txt"
        log_path.write_text(
            "LoopStudio — лог сбоя рендера\n"
            f"Время: {datetime.now().isoformat(timespec='seconds')}\n\n"
            f"Сообщение: {exc}\n\n"
            f"Команда ffmpeg:\n{exc.command}\n\n"
            f"Вывод ffmpeg (последние строки):\n{exc.stderr_tail}\n\n"
            f"Настройки задания:\n{job.to_dict()}\n",
            encoding="utf-8",
        )
        return log_path
    except OSError:
        return None


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise FFmpegCancelled("Операция отменена пользователем.")


def _render(
    job: RenderJob,
    preview: bool,
    preview_from_start: bool,
    progress: PipelineProgress,
    temp_dir: Path,
    cancel: threading.Event | None,
    started: float,
) -> RenderResult:
    # --- A. Анализ ----------------------------------------------------------
    video_path = Path(job.video_path)
    video_info = probe(video_path)
    intro = Path(job.intro_path) if job.intro_path else None
    outro = Path(job.outro_path) if job.outro_path else None
    intro_duration = probe(intro).duration if intro else 0.0
    tracks = _ordered_tracks(job)
    if preview and len(tracks) > 1 and not preview_from_start:
        offset = random.randrange(len(tracks))
        tracks = tracks[offset:] + tracks[:offset]
    track_infos: dict[Path, MediaInfo] = {}
    for track in tracks:  # длительности + теги для кредитов
        _check_cancel(cancel)  # большая папка probe'ится десятки секунд
        track_infos[track] = probe(track)

    # анализ треков (полный декод): края тишины и/или замер громкости
    analyses: dict[Path, TrackAnalysis] = {}
    need_analysis = job.trim_silence or (job.normalize and job.normalize_precise)
    if need_analysis:
        unique = list(dict.fromkeys(tracks))
        analyze_cb = progress.stage_callback(0, float(max(1, len(unique))))
        for i, track in enumerate(unique):
            _check_cancel(cancel)
            analyses[track] = analyze_track(track, track_infos[track].duration)
            analyze_cb(float(i + 1))

    def _play_duration(track: Path) -> float:
        if job.trim_silence and track in analyses:
            return analyses[track].trimmed_duration
        return track_infos[track].duration

    playlist = [(t, _play_duration(t)) for t in tracks]
    _check_cancel(cancel)
    progress.stage_done(0)

    # --- B. Резиновый таймлайн ----------------------------------------------
    target = _PREVIEW_DURATION if preview else job.duration_sec
    timeline = build_timeline(playlist, target, pause=job.pause, crossfade=job.crossfade)
    total = timeline.total_duration

    # --- C. Аудио-мастер ------------------------------------------------------
    fx = job.effects
    video_loop_crossfade = fx.loop_crossfade if fx.seamless_loop else 0.0
    ambient_layers = [
        AmbientLayer(path=Path(layer.path), volume=layer.volume)
        for layer in job.ambient
        if layer.enabled and layer.path
    ]
    if job.source_audio and video_info.has_audio:
        # звук исходника лупится с тем же периодом, что и видео-луп,
        # и стартует после интро — синхронно с видеорядом
        ambient_layers.append(
            AmbientLayer(
                path=video_path,
                volume=job.source_audio_volume,
                loop_crossfade=video_loop_crossfade,
                delay=intro_duration,
            )
        )
    # два прохода мастера (music_bus.flac + master.flac) — раньше это была
    # слепая зона проверки места: переполнение temp давало невнятную FFmpegError
    ensure_free_space(
        temp_dir, total * _MASTER_BYTES_PER_SEC * 2, what="аудио-мастера (временные файлы)"
    )
    smooth = job.smooth_start_sec if job.smooth_start else 0.0
    edge_trims = {
        track: analysis.trimmed_bounds
        for track, analysis in analyses.items()
        if job.trim_silence and (analysis.lead_silence > 0 or analysis.tail_silence > 0)
    }
    loudness = (
        {t: a.loudness for t, a in analyses.items() if a.loudness is not None}
        if job.normalize and job.normalize_precise
        else {}
    )
    master = temp_dir / "master.flac"
    build_master(
        timeline,
        MixdownSettings(
            fade_out=job.fade_out,
            normalize=job.normalize,
            music_volume=job.music_volume,
            intro_fade_in=smooth,
            pause_fade_in=_PAUSE_FADE_IN if (job.smooth_start and job.pause > 0) else 0.0,
            ambient_fade_in=smooth,
            edge_trims=edge_trims or None,
            loudness=loudness or None,
        ),
        ambient_layers,
        master,
        work_dir=temp_dir,
        on_progress=progress.stage_callback(1, total),
        cancel=cancel,
    )
    progress.stage_done(1)

    # --- D. Видео-луп ----------------------------------------------------------
    if preview:
        width, height = 1280, 720
        crf, preset = _PREVIEW_CRF, "veryfast"
    else:
        width, height = RESOLUTIONS[job.resolution]
        crf, _ = QUALITY_PRESETS[job.quality]
        preset = "medium"
    fps = min(video_info.fps or 30.0, _MAX_FPS)
    codec = detect_hw_encoder() or "libx264"  # NVENC/QSV/AMF в разы быстрее на 4K
    settings = VideoEncodeSettings(
        width=width, height=height, fps=fps, codec=codec, preset=preset, crf=crf
    )
    segment_estimate = video_info.duration - (video_loop_crossfade if fx.seamless_loop else 0.0)
    post_vf = _build_post_vf(job, width, height, fps, max(segment_estimate, 0.1))
    overlay = None
    if fx.overlay_path:
        overlay = OverlaySpec(
            path=Path(fx.overlay_path),
            opacity=fx.overlay_opacity,
            screen_blend=fx.overlay_screen_blend,
        )

    encode_cb = progress.stage_callback(2, max(segment_estimate, 0.1))
    video_track = temp_dir / "video_track.mp4"

    # прогресс склейки (этап 4): на многочасовых роликах copy-concat десятков
    # гигабайт занимает минуты — без него полоса «замирала» на весь этап
    concat_cb: ProgressCallback | None = None

    def concat_progress(units: float) -> None:
        nonlocal concat_cb
        if concat_cb is None:
            progress.stage_done(2)  # сегмент закодирован — началась склейка
            concat_cb = progress.stage_callback(3, total)
        concat_cb(units)

    build_looped_video(
        video_path,
        total,
        video_track,
        settings,
        work_dir=temp_dir,
        seamless=fx.seamless_loop,
        crossfade=fx.loop_crossfade,
        post_vf=post_vf,
        overlay=overlay,
        intro=intro,
        outro=outro,
        start_fade=smooth,
        extra_reserved_bytes=master.stat().st_size * 1.1,
        on_progress=encode_cb,
        on_concat_progress=concat_progress,
        cancel=cancel,
    )
    progress.stage_done(2)
    progress.stage_done(3)

    # --- E. Соединение аудио и видео (в temp, БЕЗ faststart) -------------------
    title = _render_title(job, total, preview)
    chapters = temp_dir / "chapters.ffmeta"
    chapters.write_text(build_ffmetadata(timeline, title=title), encoding="utf-8")
    _, audio_bitrate = QUALITY_PRESETS[job.quality]
    muxed = temp_dir / "muxed.mp4"
    muxed_estimate = video_track.stat().st_size * 1.02 + 320_000 / 8 * total
    ensure_free_space(temp_dir, muxed_estimate, what="соединения аудио и видео")
    output_dir = Path(job.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_free_space(output_dir, muxed_estimate * 1.05, what="сохранения готового ролика")
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    mux_args: list[str] = [
        "-i",
        str(video_track),
        "-i",
        str(master),
        "-f",
        "ffmetadata",
        "-i",
        str(chapters),
        "-map",
        "0:v",
        "-map",
        "1:a",
    ]
    if job.clean_metadata:
        # только глобальные метаданные: голый -map_metadata -1 стирает и заголовки глав
        mux_args += ["-map_metadata:g", "-1"]
    mux_args += [
        "-map_chapters",
        "2",
        "-metadata",
        f"title={title}",
        "-metadata",
        f"creation_time={stamp}",
        "-metadata",
        "encoder=LoopStudio",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        str(muxed),
    ]
    run_ffmpeg(mux_args, on_progress=progress.stage_callback(4, total), cancel=cancel)
    progress.stage_done(4)
    # видеоряд и мастер больше не нужны — освобождаем место до финализации
    video_track.unlink(missing_ok=True)
    master.unlink(missing_ok=True)

    # --- F+G. Финализация (faststart) сразу в папку сохранения + треклист ------
    stem = _sanitize_filename(title)
    if job.package_folder and not preview:
        # пакет-папка: mp4 + треклист + кредиты + паспорт + проект одного
        # ролика лежат вместе — серийный день не сваливает всё в одну кучу
        dest_dir = _unique_dir(output_dir, stem)
        dest_dir.mkdir(parents=True, exist_ok=True)
    else:
        dest_dir = output_dir
    output_path = _unique_path(dest_dir, stem, ".mp4")
    warnings: list[str] = []
    try:
        run_ffmpeg(
            [
                "-i",
                str(muxed),
                "-map",
                "0",
                "-map_metadata",
                "0",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            on_progress=progress.stage_callback(5, total),
            cancel=cancel,
        )
        muxed.unlink(missing_ok=True)
    except FFmpegCancelled:
        # отмена на финализации: ffmpeg уже писал в папку пользователя —
        # не оставлять там битый файл (и пустую пакет-папку)
        output_path.unlink(missing_ok=True)
        if dest_dir != output_dir:
            with contextlib.suppress(OSError):
                dest_dir.rmdir()
        raise
    except FFmpegError:
        # файл полностью готов, не хватило только «быстрого старта» — отдаём как есть
        output_path.unlink(missing_ok=True)
        shutil.move(str(muxed), output_path)
        warnings.append(
            "Ролик сохранён без «быстрого старта» (финализация не удалась) — "
            "на качество и содержимое это не влияет."
        )
    tracklist_path = output_path.with_name(f"{output_path.stem} — трек-лист.txt")
    tracklist_path.write_text(build_tracklist_text(timeline), encoding="utf-8")
    if not preview:
        # кредиты артистов из тегов + автокопия проекта: любой старый ролик
        # переиздаётся без реконструкции настроек по памяти
        credits_path = output_path.with_name(f"{output_path.stem} — кредиты.txt")
        pairs = [(t, track_infos[t]) for t in tracks]
        try:  # сервисные артефакты не отменяют рендер, но их отсутствие — не молча
            credits_path.write_text(build_credits_text(unique_tracks(pairs)), encoding="utf-8")
        except OSError:
            warnings.append("Не удалось записать файл кредитов рядом с роликом.")
        try:
            job.save(output_path.with_suffix(".lsproj"))
        except OSError:
            warnings.append("Не удалось записать копию проекта (.lsproj) рядом с роликом.")

    # --- H. Автопроверка файла и паспорт рендера (только полный рендер) --------
    # Аддитивный шаг: читает готовый файл и пишет два текстовых артефакта,
    # кодирование/mux/финализация выше не меняются.
    verify_report: VerifyReport | None = None
    passport_path: Path | None = None
    if not preview:
        verify_report = verify_output(output_path, total, cancel=cancel)
        loop_period = max(segment_estimate, 0.0)
        passport_path = output_path.with_name(f"{output_path.stem} — паспорт.txt")
        try:
            passport_path.write_text(
                build_passport_text(
                    job=job,
                    timeline=timeline,
                    output_path=output_path,
                    calculated_duration=total,
                    codec=codec,
                    elapsed_sec=time.monotonic() - started,
                    warnings=warnings,
                    verify=verify_report,
                    loop_period=loop_period,
                    intro_duration=intro_duration,
                ),
                encoding="utf-8",
            )
        except OSError:
            passport_path = None  # паспорт — сервис: его сбой не отменяет рендер
    progress.stage_done(5)

    if verify_report is not None and not verify_report.ok:
        raise VerifyError(
            "Готовый файл не прошёл автопроверку: "
            + "; ".join(verify_report.problems)
            + f".\nФайл сохранён ({output_path.name}), но публиковать его не стоит — "
            "подробности в паспорте рядом с файлом."
        )

    return RenderResult(
        output_path=output_path,
        tracklist_path=tracklist_path,
        duration=total,
        timeline=timeline,
        warnings=tuple(warnings),
        passport_path=passport_path,
    )
