"""Метаданные выходного файла — этап F пайплайна.

Политика (окончательное решение владельца): только ПОЛНАЯ очистка и
нейтральная подстановка. Никакой рандомизации или «правдоподобных» значений.

Нейтральные значения: title = название ролика, creation_time = реальное время
рендера, encoder = LoopStudio. Главы (треки) вписываются здесь же — очистка
и подстановка выполняются одним быстрым ремуксом без перекодирования.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from core.media.ffmpeg import run_ffmpeg


def apply_metadata(
    source: Path,
    output: Path,
    *,
    title: str,
    clean: bool,
    chapters_file: Path | None = None,
    creation_time: datetime | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Переписать метаданные ремуксом (``-c copy``, секунды даже на 10-часовом файле).

    :param clean: True — стереть все метаданные и главы источника
        (``-map_metadata -1 -map_chapters -1``) перед подстановкой нейтральных.
    :param chapters_file: ffmetadata-файл с главами (см. tracklist.build_ffmetadata).
    """
    stamp = (creation_time or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    args: list[str] = ["-i", str(source)]
    if chapters_file is not None:
        args += ["-f", "ffmetadata", "-i", str(chapters_file)]
    args += ["-map", "0"]
    if clean:
        args += ["-map_metadata", "-1"]
    if chapters_file is not None:
        args += ["-map_chapters", "1"]
    elif clean:
        args += ["-map_chapters", "-1"]
    args += [
        "-metadata",
        f"title={title}",
        "-metadata",
        f"creation_time={stamp}",
        "-metadata",
        "encoder=LoopStudio",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run_ffmpeg(args, cancel=cancel)
