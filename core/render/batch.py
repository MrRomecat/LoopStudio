"""Пакетный режим: одна конфигурация × несколько папок музыки = несколько заданий.

Серийный понедельник криейтера: 5 подборок музыки → 5 роликов одной кнопкой.
Модуль — чистые функции без Qt: диалог выбора папок живёт в UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.audio.playlist import scan_music_folder
from core.render.job import RenderJob


@dataclass(frozen=True, slots=True)
class MusicFolder:
    """Папка-кандидат для пакетного задания."""

    path: Path
    track_count: int


def list_music_subfolders(parent: Path) -> list[MusicFolder]:
    """Подпапки родительской папки, в которых есть поддерживаемые треки.

    Подпапка = один плейлист = один ролик. Папки без треков не предлагаются.
    """
    if not parent.is_dir():
        raise NotADirectoryError(f"Папка не найдена: {parent}")
    folders: list[MusicFolder] = []
    for child in sorted(parent.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        try:
            tracks = scan_music_folder(child)
        except NotADirectoryError:
            continue
        if tracks:
            folders.append(MusicFolder(path=child, track_count=len(tracks)))
    return folders


def clone_job_for_folder(job: RenderJob, folder: Path) -> RenderJob:
    """Клон задания под другую папку музыки (для пакетной постановки).

    - ручной порядок треков сбрасывается: он привязан к исходной папке;
    - если в шаблоне названия нет ``{folder}``, он добавляется — иначе все
      ролики пакета получили бы одинаковые имена файлов.
    """
    clone = RenderJob.from_dict(job.to_dict())  # глубокая копия
    clone.music_dir = str(folder)
    clone.track_order = []
    clone.excluded_tracks = []  # исключения тоже привязаны к исходной папке
    if "{folder}" not in clone.title_template:
        clone.title_template = f"{clone.title_template.strip()} — {{folder}}"
    return clone
