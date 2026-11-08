"""Сборка мастер-аудио (master.flac) — этап C пайплайна.

Мастер собирается В ДВА ПРОХОДА (архитектурное требование, не оптимизация):

1. Музыкальная шина: треки + повторы (asplit) + паузы/фейды/кроссфейды +
   LUFS → music_bus.flac. Без слоёв этот проход сразу даёт мастер.
2. Слои: готовая шина + зацикленные фильтром ``aloop`` юниты слоёв → amix.

Объединять проходы НЕЛЬЗЯ: комбинация «asplit-повторы + бесконечный aloop +
amix» в одном графе заклинивает планировщик ffmpeg 8 на стыке повторов
плейлиста (взаимная блокировка очередей → -22 Invalid argument; проверено
бинарным поиском на реальном 3.5-часовом проекте — каждая часть по
отдельности работает, вместе — дедлок). ``-stream_loop`` для слоёв также
не используется: перезапуски файла демуксером нестабильны на длинных лупах.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.audio.analyze import LoudnessStats
from core.audio.timeline import Timeline
from core.media.ffmpeg import ProgressCallback, run_ffmpeg
from core.media.probe import probe

_TRACK_PREP = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
_LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


@dataclass(frozen=True, slots=True)
class AmbientLayer:
    """Один зацикленный слой: файл, громкость (1.0 = 100 %), кроссфейд лупа.

    ``delay`` — тишина перед стартом слоя (сек): звук исходного видео
    начинается после интро вместе с видеорядом.
    """

    path: Path
    volume: float = 1.0
    loop_crossfade: float = 1.0
    delay: float = 0.0


@dataclass(frozen=True, slots=True)
class MixdownSettings:
    """Настройки мастер-дорожки (шаги 2–4 экрана)."""

    fade_out: float = 0.0
    normalize: bool = False
    music_volume: float = 1.0
    ambient_end_fade: float = 3.0
    #: «плавный старт»: fade-in первого трека (сек, 0 = выкл)
    intro_fade_in: float = 0.0
    #: мягкое вступление каждого трека после паузы-тишины (сек, 0 = выкл)
    pause_fade_in: float = 0.0
    #: fade-in эмбиент-слоёв в начале ролика (сек, 0 = выкл)
    ambient_fade_in: float = 0.0
    #: обрезка тишины: путь трека → (start, end) для atrim; None = не резать
    edge_trims: dict[Path, tuple[float, float]] | None = None
    #: точная нормализация: путь → замер loudnorm (linear=true); None = быстрая
    loudness: dict[Path, LoudnessStats] | None = None


def _prepare_ambient_unit(
    layer: AmbientLayer, work_dir: Path, index: int, cancel: threading.Event | None
) -> tuple[Path, float]:
    """Сделать из файла слоя бесшовный луп-юнит (хвост кроссфейдится в начало).

    Два точных шага: сначала аудио слоя перегоняется в единый формат (у видео
    аудио-дорожка часто чуть короче самого файла — доверять длительности
    контейнера нельзя), затем по ФАКТИЧЕСКОЙ длительности строится кроссфейд.
    Возвращает (файл юнита, его точная длительность).
    """
    raw = work_dir / f"ambient_raw_{index}.flac"
    run_ffmpeg(
        ["-i", str(layer.path), "-vn", "-af", _TRACK_PREP, "-c:a", "flac", str(raw)], cancel=cancel
    )
    raw_duration = probe(raw).duration
    crossfade = min(layer.loop_crossfade, max(0.0, (raw_duration - 0.6) / 2))
    if crossfade < 0.1:
        return raw, raw_duration  # слишком короткий файл — лупим как есть

    unit = work_dir / f"ambient_unit_{index}.flac"
    graph = (
        f"[0:a]asplit=3[s0][s1][s2];"
        f"[s0]atrim={crossfade:.3f}:{raw_duration - crossfade:.3f},asetpts=PTS-STARTPTS[body];"
        f"[s1]atrim=start={raw_duration - crossfade:.3f},asetpts=PTS-STARTPTS[tail];"
        f"[s2]atrim=0:{crossfade:.3f},asetpts=PTS-STARTPTS[head];"
        f"[tail][head]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri[xf];"
        f"[body][xf]concat=n=2:v=0:a=1[out]"
    )
    run_ffmpeg(
        ["-i", str(raw), "-filter_complex", graph, "-map", "[out]", "-c:a", "flac", str(unit)],
        cancel=cancel,
    )
    return unit, probe(unit).duration


def _track_prep_chain(path: Path, settings: MixdownSettings) -> str:
    """Цепочка подготовки одного входа: [трим тишины,] [нормализация,] формат.

    Точная нормализация — тот же loudnorm, но с замерами (linear=true):
    статическая линейная коррекция без «дыхания» динамического режима.
    """
    parts: list[str] = []
    trims = settings.edge_trims or {}
    if path in trims:
        start, end = trims[path]
        parts.append(f"atrim={start:.3f}:{end:.3f},asetpts=PTS-STARTPTS")
    if settings.normalize:
        stats = (settings.loudness or {}).get(path)
        if stats is not None:
            parts.append(
                f"{_LOUDNORM}:measured_I={stats.input_i:.2f}"
                f":measured_TP={stats.input_tp:.2f}"
                f":measured_LRA={stats.input_lra:.2f}"
                f":measured_thresh={stats.input_thresh:.2f}:linear=true"
            )
        else:
            parts.append(_LOUDNORM)
    parts.append(_TRACK_PREP)
    return ",".join(parts)


def _music_graph(timeline: Timeline, settings: MixdownSettings) -> list[str]:
    """Часть filter-графа: подготовка треков и сборка музыкальной шины [musicv]."""
    entries = timeline.entries
    n = len(entries)

    input_index: dict[Path, int] = {}
    for entry in entries:
        input_index.setdefault(entry.path, len(input_index))
    use_counts = Counter(entry.path for entry in entries)

    lines: list[str] = []
    for path, idx in input_index.items():
        prep = _track_prep_chain(path, settings)
        k = use_counts[path]
        if k == 1:
            lines.append(f"[{idx}:a]{prep}[u{idx}_0]")
        else:
            outs = "".join(f"[u{idx}_{j}]" for j in range(k))
            lines.append(f"[{idx}:a]{prep},asplit={k}{outs}")

    seen: defaultdict[Path, int] = defaultdict(int)
    labels: list[str] = []
    for j, entry in enumerate(entries):
        src = f"u{input_index[entry.path]}_{seen[entry.path]}"
        seen[entry.path] += 1
        fades: list[str] = []
        # «плавный старт»: первый трек ролика вступает из тишины
        if j == 0 and settings.intro_fade_in > 0:
            fades.append(f"afade=t=in:st=0:d={settings.intro_fade_in:.3f}")
        # при паузах каждый следующий трек вступает мягко — не будит зрителя
        if j > 0 and timeline.pause > 0 and settings.pause_fade_in > 0:
            fades.append(f"afade=t=in:st=0:d={settings.pause_fade_in:.3f}")
        fade = settings.fade_out
        # при кроссфейде затухание между треками делает сам acrossfade;
        # fade-out тогда остаётся только у последнего трека (конец ролика)
        if fade > 0 and (timeline.crossfade == 0 or j == n - 1):
            start = max(0.0, entry.duration - fade)
            fades.append(f"afade=t=out:st={start:.3f}:d={fade:.3f}")
        if fades:
            lines.append(f"[{src}]{','.join(fades)}[e{j}]")
            labels.append(f"e{j}")
        else:
            labels.append(src)

    if n == 1:
        lines.append(f"[{labels[0]}]anull[music]")
    elif timeline.crossfade > 0:
        prev = labels[0]
        for j in range(1, n):
            out = "music" if j == n - 1 else f"x{j}"
            lines.append(
                f"[{prev}][{labels[j]}]acrossfade=d={timeline.crossfade:.3f}:c1=tri:c2=tri[{out}]"
            )
            prev = out
    elif timeline.pause > 0:
        parts: list[str] = []
        for j, label in enumerate(labels):
            parts.append(f"[{label}]")
            if j < n - 1:
                lines.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{timeline.pause:.3f},{_TRACK_PREP}[sil{j}]"
                )
                parts.append(f"[sil{j}]")
        lines.append(f"{''.join(parts)}concat=n={2 * n - 1}:v=0:a=1[music]")
    else:
        lines.append(f"{''.join(f'[{lbl}]' for lbl in labels)}concat=n={n}:v=0:a=1[music]")

    lines.append(f"[music]volume={settings.music_volume:.3f}[musicv]")
    return lines


def build_master(
    timeline: Timeline,
    settings: MixdownSettings,
    ambient: Sequence[AmbientLayer],
    output: Path,
    *,
    work_dir: Path,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Собрать master.flac длиной ровно ``timeline.total_duration``.

    :param work_dir: папка для filter-скрипта и эмбиент-юнитов (temp рендера).
    """
    total = timeline.total_duration
    unique_tracks = list(dict.fromkeys(entry.path for entry in timeline.entries))
    phases = 1 if not ambient else 2

    def phase_progress(phase: int) -> ProgressCallback | None:
        if on_progress is None:
            return None

        def scaled(seconds: float) -> None:
            assert on_progress is not None
            on_progress((phase * total + seconds) / phases)

        return scaled

    # --- проход 1: музыкальная шина (без слоёв это сразу мастер) --------------
    music_lines = _music_graph(timeline, settings)
    if not ambient:
        # level=0 обязателен: авто-уровень alimiter (включён по умолчанию)
        # домножает выход до 0 dBFS и молча съедает весь запас 0.97
        music_lines.append("[musicv]alimiter=limit=0.97:level=0[master]")
        bus_output, bus_label = output, "master"
    else:
        bus_output, bus_label = work_dir / "music_bus.flac", "musicv"
    script = work_dir / "master_filter.txt"
    script.write_text(";\n".join(music_lines), encoding="utf-8")
    args: list[str] = []
    for track in unique_tracks:
        args += ["-i", str(track)]
    args += [
        "-filter_complex_script",
        str(script),
        "-map",
        f"[{bus_label}]",
        "-c:a",
        "flac",
        "-ar",
        "48000",
        str(bus_output),
    ]
    run_ffmpeg(args, on_progress=phase_progress(0), cancel=cancel)
    if not ambient:
        return output

    # --- проход 2: готовая шина + зацикленные слои → amix ---------------------
    units = [_prepare_ambient_unit(layer, work_dir, i, cancel) for i, layer in enumerate(ambient)]
    lines = [f"[0:a]{_TRACK_PREP}[musicv]"]
    amb_labels: list[str] = []
    for i, layer in enumerate(ambient):
        _, unit_duration = units[i]
        end_fade = min(settings.ambient_end_fade, total / 2)
        fade_start = max(0.0, total - end_fade)
        # луп фильтром в памяти: size с запасом (лупится фактически прочитанное)
        loop_size = int(unit_duration * 48000) + 48000
        delay = ""
        if layer.delay > 0:
            delay = f"adelay={round(layer.delay * 1000)}:all=1,"
        fade_in = ""
        if settings.ambient_fade_in > 0:
            # «плавный старт»: слой вступает мягко (после своего delay)
            fade_in = f"afade=t=in:st={layer.delay:.3f}:d={settings.ambient_fade_in:.3f},"
        lines.append(
            f"[{i + 1}:a]{_TRACK_PREP},aloop=loop=-1:size={loop_size},"
            f"{delay}atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
            f"volume={layer.volume:.3f},{fade_in}"
            f"afade=t=out:st={fade_start:.3f}:d={end_fade:.3f}[amb{i}]"
        )
        amb_labels.append(f"amb{i}")
    inputs = "[musicv]" + "".join(f"[{label}]" for label in amb_labels)
    lines.append(
        f"{inputs}amix=inputs={1 + len(amb_labels)}:duration=first:normalize=0,"
        f"alimiter=limit=0.97:level=0[master]"
    )
    mix_script = work_dir / "mix_filter.txt"
    mix_script.write_text(";\n".join(lines), encoding="utf-8")
    mix_args: list[str] = ["-i", str(bus_output)]
    for unit_path, _ in units:
        mix_args += ["-i", str(unit_path)]
    mix_args += [
        "-filter_complex_script",
        str(mix_script),
        "-map",
        "[master]",
        "-c:a",
        "flac",
        "-ar",
        "48000",
        str(output),
    ]
    run_ffmpeg(mix_args, on_progress=phase_progress(1), cancel=cancel)
    return output
