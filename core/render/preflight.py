"""Пре-флайт: проверки и смета ДО постановки задания в очередь.

Отвечает на три вопроса до часов рендера, а не на 70% ночной очереди:
1. Все ли входные файлы читаются?
2. Какой будет фактическая длительность (резиновый таймлайн) и хватит ли места?
3. Нет ли «тихих» конфликтов настроек (пауза глушит кроссфейд, вертикальное
   видео в кадре 16:9, .mov-оверлей без альфы)?

Пре-флайт ничего не блокирует жёстко: смета места приблизительная, поэтому
решение «Всё равно рендерить» всегда остаётся за пользователем. Внутренние
проверки места по ходу рендера (ensure_free_space) остаются как защита
в глубину — этот модуль их не заменяет.

Модуль не трогает конвейер: только чтение (ffprobe) и арифметика.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.audio.timeline import TimelineError, build_timeline
from core.media.probe import MediaInfo, ProbeError, probe
from core.render.job import RESOLUTIONS, JobError, RenderJob
from core.render.pipeline import _ordered_tracks


class DiskUsage(Protocol):
    """Структурный тип результата shutil.disk_usage — важно только free."""

    @property
    def free(self) -> int: ...


ProbeFn = Callable[[Path], MediaInfo]
DiskUsageFn = Callable[[Path], DiskUsage]

_PROBE_WORKERS = 4

#: оценка видеопотока, Мбит/с: разрешение → режим качества.
#: Калибровано по CRF 16/18/21 (hw-кодеры выровнены: cq = crf + 5).
_VIDEO_MBPS: dict[str, dict[str, float]] = {
    "720p": {"source": 8.0, "high": 5.0, "standard": 3.5},
    "1080p": {"source": 12.0, "high": 8.0, "standard": 5.5},
    "1440p": {"source": 20.0, "high": 13.0, "standard": 9.0},
    "2160p": {"source": 40.0, "high": 26.0, "standard": 18.0},
}
_AUDIO_MBPS = 0.32
#: master.flac: 48 кГц stereo 24 бит × типичная степень сжатия FLAC
_MASTER_BYTES_PER_SEC = 48_000 * 2 * 3 * 0.7


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Итог пре-флайта: смета + три уровня сообщений."""

    predicted_duration: float | None  # фактическая длительность ролика, сек
    est_output_bytes: int | None
    est_temp_bytes: int | None
    infos: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()  # жёлтые: рендер пройдёт, результат может удивить
    blockers: tuple[str, ...] = ()  # красные: рендер почти наверняка упадёт

    @property
    def clean(self) -> bool:
        """Нет ни предупреждений, ни блокеров — можно ставить в очередь молча."""
        return not self.warnings and not self.blockers


def _hms(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _gb(size: float) -> str:
    return f"{size / 1024**3:.1f} ГБ"


def _estimate_output_bytes(job: RenderJob, duration: float) -> int:
    mbps = _VIDEO_MBPS[job.resolution][job.quality] + _AUDIO_MBPS
    return int(mbps * 1_000_000 / 8 * duration)


def _estimate_temp_bytes(output_bytes: int, duration: float) -> int:
    # пик temp: видеоряд + muxed (≈ 2×итог) + music_bus.flac + master.flac
    master = _MASTER_BYTES_PER_SEC * duration
    return int(output_bytes * 2.2 + master * 2)


def _probe_many(
    paths: Sequence[Path], probe_fn: ProbeFn
) -> tuple[dict[Path, MediaInfo], list[str]]:
    """Проанализировать файлы параллельно; ошибки → человекочитаемые блокеры."""
    infos: dict[Path, MediaInfo] = {}
    problems: list[str] = []

    def one(path: Path) -> tuple[Path, MediaInfo | None, str | None]:
        try:
            return path, probe_fn(path), None
        except ProbeError as exc:
            return path, None, str(exc)

    if not paths:
        return infos, problems
    with ThreadPoolExecutor(max_workers=min(_PROBE_WORKERS, len(paths))) as pool:
        for path, info, error in pool.map(one, paths):
            if info is not None:
                infos[path] = info
            elif error is not None:
                problems.append(error)
    return infos, problems


def _check_output_writable(output_dir: str) -> str | None:
    """Папку сохранения можно создать и в неё можно писать? None = всё хорошо."""
    try:
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        probe_file = folder / f".loopstudio_write_test_{uuid.uuid4().hex[:8]}"
        probe_file.write_bytes(b"ok")
        probe_file.unlink()
    except OSError as exc:
        return (
            f"В папку сохранения не получается записать файл: {output_dir}\n"
            f"({exc}). Проверьте, что диск подключён и папка не только для чтения (шаг 7)."
        )
    return None


def _drive_root(path: Path) -> Path:
    return Path(path.drive + "\\") if path.drive else path


def run_preflight(
    job: RenderJob,
    *,
    pending: Sequence[RenderJob] = (),
    probe_fn: ProbeFn = probe,
    disk_usage: DiskUsageFn = shutil.disk_usage,
) -> PreflightReport:
    """Полный пре-флайт задания. Блокирующий вызов (секунды) — запускать в фоне.

    :param pending: задания, уже ожидающие в очереди, — их место учитывается
        в смете («на 5-е задание может не хватить»).
    :param probe_fn: инъекция для кэша контроллера и тестов.
    """
    infos: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []

    # --- входные файлы -------------------------------------------------------
    video_path = Path(job.video_path)
    to_probe: list[Path] = [video_path]
    for clip in (job.intro_path, job.outro_path):
        if clip:
            to_probe.append(Path(clip))
    for layer in job.ambient:
        if layer.enabled and layer.path:
            to_probe.append(Path(layer.path))
    if job.effects.overlay_path:
        to_probe.append(Path(job.effects.overlay_path))
    try:
        tracks = _ordered_tracks(job)
        if not tracks:
            blockers.append(
                f"В папке с музыкой нет поддерживаемых треков: {job.music_dir} (шаг 2)."
            )
    except JobError as exc:  # например, «все треки исключены»
        tracks = []
        blockers.append(str(exc))
    except (OSError, NotADirectoryError):
        tracks = []
        blockers.append(f"Папка с музыкой недоступна: {job.music_dir} (шаг 2).")
    to_probe.extend(tracks)

    media, probe_problems = _probe_many(to_probe, probe_fn)
    blockers.extend(probe_problems)

    # --- прогноз фактической длительности ------------------------------------
    predicted: float | None = None
    playlist = [(t, media[t].duration) for t in tracks if t in media]
    if playlist and len(playlist) == len(tracks):
        try:
            timeline = build_timeline(
                playlist, job.duration_sec, pause=job.pause, crossfade=job.crossfade
            )
        except TimelineError as exc:
            blockers.append(str(exc))
        else:
            # интро и аутро НЕ удлиняют ролик: видеоряд собирается ровно в длину
            # мастер-дорожки, вступление и концовка занимают часть этого времени
            predicted = timeline.total_duration
            overshoot = predicted - job.duration_sec
            note = f" (на {_hms(overshoot)} длиннее заданного)" if overshoot >= 1 else ""
            trim_note = (
                " Включена обрезка тишины: точная длительность станет известна "
                "после анализа треков в начале рендера."
                if job.trim_silence
                else ""
            )
            infos.append(
                f"Итоговая длительность: ~{_hms(predicted)}{note} — "
                f"музыка никогда не обрезается, последний трек доигрывает целиком." + trim_note
            )
            clips_duration = 0.0
            for clip in (job.intro_path, job.outro_path):
                clip_info = media.get(Path(clip)) if clip else None
                if clip_info is not None:
                    clips_duration += clip_info.duration
            if clips_duration > 0 and clips_duration >= predicted:
                blockers.append(
                    f"Интро и аутро вместе ({_hms(clips_duration)}) длиннее всего ролика "
                    f"({_hms(predicted)}) — увеличьте длительность (шаг 6) или "
                    f"уберите интро/аутро (шаг 1)."
                )

    # --- тихие конфликты настроек --------------------------------------------
    if job.track_order and tracks:
        actual_names = {t.name for t in tracks}
        saved_names = set(job.track_order)
        lost = saved_names - actual_names
        added = actual_names - saved_names
        if lost or added:
            parts = []
            if lost:
                parts.append(f"{len(lost)} трек(ов) из сохранённого порядка не найдено")
            if added:
                parts.append(f"{len(added)} новых добавятся в конец")
            warnings.append(
                "Плейлист изменился со времени настройки: "
                + "; ".join(parts)
                + ". Проверьте список треков (шаг 2)."
            )

    if job.pause > 0 and job.crossfade > 0:
        warnings.append(
            f"Пауза ({job.pause:g} с) отключает кроссфейд ({job.crossfade:g} с): "
            f"трекам, разделённым тишиной, нечего перекрывать. Останется только пауза. "
            f"Уберите паузу, если нужны плавные переходы (шаг 2)."
        )

    video_info = media.get(video_path)
    if video_info is not None and video_info.width and video_info.height:
        width, height = video_info.width, video_info.height
        target_w, target_h = RESOLUTIONS[job.resolution]
        if height > width:
            warnings.append(
                f"Видео вертикальное ({width}×{height}), а ролик — горизонтальный "
                f"{target_w}×{target_h}: по бокам будут широкие чёрные полосы. "
                f"Для длинных роликов YouTube нужен горизонтальный исходник (шаг 1)."
            )
        else:
            aspect = width / height
            if abs(aspect - 16 / 9) > 0.02:
                warnings.append(
                    f"Пропорции видео {width}×{height} отличаются от 16:9 — "
                    f"недостающее пространство будет заполнено чёрными полями (шаг 1)."
                )
        if video_info.fps and video_info.fps > 60:
            infos.append(
                f"Частота кадров исходника {video_info.fps:.0f} — "
                f"в ролике будет ограничена 60 кадр/с."
            )

    for i, layer in enumerate(job.ambient, start=1):
        if not (layer.enabled and layer.path):
            continue
        layer_info = media.get(Path(layer.path))
        if layer_info is None:
            continue
        if layer_info.duration < 0.8:
            warnings.append(
                f"Эмбиент-слой {i} очень короткий ({layer_info.duration:.1f} с) — "
                f"он будет лупиться встык, возможен слышимый щелчок на стыке (шаг 3)."
            )
        elif layer_info.duration < 2.6:
            warnings.append(
                f"Эмбиент-слой {i} короткий ({layer_info.duration:.1f} с) — "
                f"кроссфейд лупа будет урезан, стык может быть слышен (шаг 3)."
            )

    overlay_path = job.effects.overlay_path
    if overlay_path:
        suffix = Path(overlay_path).suffix.lower()
        if suffix == ".mov":
            warnings.append(
                "Оверлей .mov будет наложен в режиме «screen» (тёмное станет прозрачным). "
                "Если у файла есть альфа-канал, конвертируйте его в .webm — "
                "иначе полупрозрачность потеряется (шаг 4)."
            )
        overlay_info = media.get(Path(overlay_path))
        if (
            overlay_info is not None
            and overlay_info.width
            and overlay_info.height
            and abs(overlay_info.width / overlay_info.height - 16 / 9) > 0.05
        ):
            warnings.append(
                f"Пропорции оверлея {overlay_info.width}×{overlay_info.height} "
                f"отличаются от 16:9 — он будет растянут на весь кадр (шаг 4)."
            )

    # --- папка сохранения и место --------------------------------------------
    est_output: int | None = None
    est_temp: int | None = None
    if job.output_dir:
        problem = _check_output_writable(job.output_dir)
        if problem is not None:
            blockers.append(problem)

    duration_for_size = predicted if predicted is not None else job.duration_sec
    if job.output_dir:
        est_output = _estimate_output_bytes(job, duration_for_size)
        est_temp = _estimate_temp_bytes(est_output, duration_for_size)
        infos.append(
            f"Оценка размера: итоговый файл ~{_gb(est_output)}, "
            f"временные файлы рендера — до {_gb(est_temp)} (оценка приблизительная)."
        )
        try:
            temp_root = Path(tempfile.gettempdir())
            out_root = Path(job.output_dir)
            same_drive = _drive_root(temp_root) == _drive_root(out_root)
            temp_free = disk_usage(temp_root).free
            out_free = disk_usage(out_root).free

            temp_need = est_temp + (est_output if same_drive else 0)
            if temp_free < temp_need:
                blockers.append(
                    f"Похоже, не хватит места для временных файлов на диске "
                    f"{temp_root.drive or temp_root}: нужно ~{_gb(temp_need)}, "
                    f"свободно {_gb(temp_free)}. Освободите место до старта."
                )
            elif not same_drive and out_free < est_output * 1.1:
                blockers.append(
                    f"Похоже, не хватит места для готового ролика на диске "
                    f"{out_root.drive or out_root}: нужно ~{_gb(est_output * 1.1)}, "
                    f"свободно {_gb(out_free)}. Освободите место или смените папку (шаг 7)."
                )

            if pending:
                queue_bytes = est_output
                for other in pending:
                    other_dur = other.duration_sec
                    queue_bytes += _estimate_output_bytes(other, other_dur)
                if out_free < queue_bytes:
                    warnings.append(
                        f"Вместе с {len(pending)} заданиями, уже ожидающими в очереди, "
                        f"понадобится ~{_gb(queue_bytes)} на диске "
                        f"{out_root.drive or out_root} (свободно {_gb(out_free)}) — "
                        f"последним заданиям может не хватить места."
                    )
        except OSError:
            pass  # смета — сервис; недоступный диск уже поймают другие проверки

    return PreflightReport(
        predicted_duration=predicted,
        est_output_bytes=est_output,
        est_temp_bytes=est_temp,
        infos=tuple(infos),
        warnings=tuple(warnings),
        blockers=tuple(blockers),
    )
