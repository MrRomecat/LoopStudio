"""«Проверить швы» — нарезка контрольных стыков видео до полного рендера.

Шов видео-лупа — причина №1 закрытой вкладки, а найти его в готовом
7-часовом файле можно было только зная длину исходника наизусть. Нарезка
показывает каждый тип стыка за пару минут рендера вместо лотереи:

- шов лупа: последние секунды сегмента → первые секунды (как увидит зритель);
- интро → луп и луп → аутро (если заданы).

Секции разделены короткими чёрными паузами. Всё собирается тем же
кодировщиком, что и превью (720p, быстрый пресет); сам конвейер рендера
не затрагивается — используются готовые кирпичи looper'а.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from core.media.ffmpeg import ProgressCallback, run_ffmpeg
from core.media.probe import probe
from core.render.job import RenderJob
from core.render.pipeline import _build_post_vf
from core.video.looper import VideoEncodeSettings, concat_files, conform_clip, make_loop_segment

_PIECE_SEC = 4.0  # сколько секунд показываем с каждой стороны стыка
_CLIP_EDGE_SEC = 3.0  # для интро/аутро
_GAP_SEC = 0.7  # чёрная пауза между секциями


def _cut_piece(
    source: Path,
    output: Path,
    settings: VideoEncodeSettings,
    start: float,
    duration: float,
    *,
    cancel: threading.Event | None,
) -> None:
    run_ffmpeg(
        [
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            *settings.encode_args(),
            str(output),
        ],
        cancel=cancel,
    )


def _black_gap(
    output: Path, settings: VideoEncodeSettings, *, cancel: threading.Event | None
) -> None:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:size={settings.width}x{settings.height}"
            f":rate={settings.fps:g}:duration={_GAP_SEC}",
            *settings.encode_args(),
            str(output),
        ],
        cancel=cancel,
    )


def build_seams_preview(
    job: RenderJob,
    output: Path,
    *,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Собрать нарезку швов (720p, без звука). Блокирующий вызов — в фон.

    Эффекты (Кен Бёрнс, фильтр, виньетка, зерно, оверлей) применяются как
    в финальном рендере — швы смотрим в том виде, в котором их увидит зритель.
    """
    video_path = Path(job.video_path)
    video_info = probe(video_path)
    fx = job.effects
    settings = VideoEncodeSettings(
        width=1280,
        height=720,
        fps=min(video_info.fps or 30.0, 60.0),
        codec="libx264",
        preset="veryfast",
        crf=28,
    )
    crossfade = fx.loop_crossfade if fx.seamless_loop else 0.0
    segment_estimate = max(video_info.duration - crossfade, 0.1)
    post_vf = _build_post_vf(job, settings.width, settings.height, settings.fps, segment_estimate)

    with tempfile.TemporaryDirectory(prefix="loopstudio_seams_") as work_str:
        work = Path(work_str)
        segment = work / "segment.mp4"
        overlay = None  # оверлей у нарезки опускаем: он не влияет на геометрию шва
        make_loop_segment(
            video_path,
            segment,
            settings,
            seamless=fx.seamless_loop,
            crossfade=fx.loop_crossfade,
            post_vf=post_vf,
            overlay=overlay,
            on_progress=on_progress,
            cancel=cancel,
        )
        seg_duration = probe(segment).duration
        piece = min(_PIECE_SEC, max(0.5, seg_duration / 2))

        parts: list[Path] = []

        def add_gap(index: int) -> None:
            gap = work / f"gap_{index}.mp4"
            _black_gap(gap, settings, cancel=cancel)
            parts.append(gap)

        # 1) шов лупа: конец сегмента → начало (дважды, чтобы вглядеться)
        tail_piece = work / "loop_tail.mp4"
        head_piece = work / "loop_head.mp4"
        _cut_piece(segment, tail_piece, settings, seg_duration - piece, piece, cancel=cancel)
        _cut_piece(segment, head_piece, settings, 0.0, piece, cancel=cancel)
        parts += [tail_piece, head_piece]
        add_gap(1)
        parts += [tail_piece, head_piece]

        # 2) интро → луп
        if job.intro_path:
            intro_part = work / "intro.mp4"
            intro_duration = conform_clip(Path(job.intro_path), intro_part, settings, cancel=cancel)
            intro_piece = work / "intro_tail.mp4"
            _cut_piece(
                intro_part,
                intro_piece,
                settings,
                max(0.0, intro_duration - _CLIP_EDGE_SEC),
                min(_CLIP_EDGE_SEC, intro_duration),
                cancel=cancel,
            )
            add_gap(2)
            parts += [intro_piece, head_piece]

        # 3) луп → аутро
        if job.outro_path:
            outro_part = work / "outro.mp4"
            outro_duration = conform_clip(Path(job.outro_path), outro_part, settings, cancel=cancel)
            outro_piece = work / "outro_head.mp4"
            _cut_piece(
                outro_part,
                outro_piece,
                settings,
                0.0,
                min(_CLIP_EDGE_SEC, outro_duration),
                cancel=cancel,
            )
            add_gap(3)
            parts += [tail_piece, outro_piece]

        concat_files(parts, output, work / "concat_list.txt", cancel=cancel)
    return output
