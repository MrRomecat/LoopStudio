"""Проверка проекта при открытии: пропавшие пути, дифф плейлиста, перепривязка.

Боль серийщика: библиотека переехала на другой диск — каждый проект чинился
перевыбором шести путей, а треки, пропавшие из папки, молча выкидывались из
плейлиста. Теперь при открытии проекта видно всё сразу, а переезд лечится
одной операцией «указать новый корень».

Модуль — чистые функции без Qt и без ffmpeg: только пути и списки.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.audio.playlist import scan_music_folder
from core.render.job import RenderJob


@dataclass(frozen=True, slots=True)
class MissingPath:
    """Один потерянный путь проекта."""

    field: str  # машинное имя поля RenderJob (для перепривязки)
    label: str  # человекочитаемая подпись («Основное видео», «Эмбиент-слой 2»)
    path: str
    is_dir: bool = False


@dataclass(frozen=True, slots=True)
class PlaylistDiff:
    """Расхождение сохранённого порядка треков с текущим содержимым папки."""

    missing: tuple[str, ...]  # были в проекте, пропали из папки
    added: tuple[str, ...]  # появились в папке, добавятся в конец

    @property
    def empty(self) -> bool:
        return not self.missing and not self.added


@dataclass(frozen=True, slots=True)
class ProjectCheck:
    """Итог проверки открытого проекта."""

    missing: tuple[MissingPath, ...]
    playlist: PlaylistDiff

    @property
    def clean(self) -> bool:
        return not self.missing and self.playlist.empty


def _iter_paths(job: RenderJob) -> list[MissingPath]:
    """Все файловые ссылки проекта с подписями (только заполненные)."""
    entries: list[MissingPath] = []
    if job.video_path:
        entries.append(MissingPath("video_path", "Основное видео (шаг 1)", job.video_path))
    if job.intro_path:
        entries.append(MissingPath("intro_path", "Интро (шаг 1)", job.intro_path))
    if job.outro_path:
        entries.append(MissingPath("outro_path", "Аутро (шаг 1)", job.outro_path))
    if job.music_dir:
        entries.append(
            MissingPath("music_dir", "Папка с музыкой (шаг 2)", job.music_dir, is_dir=True)
        )
    for i, layer in enumerate(job.ambient):
        if layer.enabled and layer.path:
            entries.append(MissingPath(f"ambient.{i}", f"Эмбиент-слой {i + 1} (шаг 3)", layer.path))
    if job.effects.overlay_path:
        entries.append(
            MissingPath("effects.overlay_path", "VFX-оверлей (шаг 4)", job.effects.overlay_path)
        )
    return entries


def check_project(job: RenderJob) -> ProjectCheck:
    """Проверить все пути проекта и дифф плейлиста. Ничего не меняет."""
    missing = tuple(
        entry
        for entry in _iter_paths(job)
        if not (Path(entry.path).is_dir() if entry.is_dir else Path(entry.path).is_file())
    )

    diff = PlaylistDiff(missing=(), added=())
    if job.music_dir and job.track_order and Path(job.music_dir).is_dir():
        actual = {p.name for p in scan_music_folder(Path(job.music_dir))}
        saved = list(job.track_order)
        missing_tracks = tuple(name for name in saved if name not in actual)
        added_tracks = tuple(sorted(actual - set(saved), key=str.lower))
        diff = PlaylistDiff(missing=missing_tracks, added=added_tracks)

    return ProjectCheck(missing=missing, playlist=diff)


def _candidate_tails(path: Path) -> list[Path]:
    """Хвосты пути по убыванию длины: «Библиотека/Дожди/шум.flac», «Дожди/шум.flac», «шум.flac»."""
    parts = path.parts
    # без буквы диска/корня; максимум 4 компонента хвоста — глубже переезд не ищем
    tail_parts = [p for p in parts if p not in (path.anchor, path.drive, "\\", "/")]
    tails: list[Path] = []
    for start in range(max(0, len(tail_parts) - 4), len(tail_parts)):
        tails.append(Path(*tail_parts[start:]))
    return tails


def relink_path(old: Path, new_root: Path, *, is_dir: bool = False) -> Path | None:
    """Найти переехавший файл/папку под новым корнем по хвосту старого пути."""
    exists = Path.is_dir if is_dir else Path.is_file
    for tail in _candidate_tails(old):
        candidate = new_root / tail
        if exists(candidate):
            return candidate
    return None


def relink_job(job: RenderJob, new_root: Path) -> tuple[RenderJob, list[str], list[MissingPath]]:
    """Перепривязать все потерянные пути проекта к новому корню одной операцией.

    Возвращает (обновлённый job, что починили, что так и не нашли).
    Исходный job не меняется.
    """
    data = job.to_dict()
    fixed: list[str] = []
    still_missing: list[MissingPath] = []
    for entry in check_project(job).missing:
        found = relink_path(Path(entry.path), new_root, is_dir=entry.is_dir)
        if found is None:
            still_missing.append(entry)
            continue
        if entry.field.startswith("ambient."):
            index = int(entry.field.split(".", 1)[1])
            data["ambient"][index]["path"] = str(found)
        elif entry.field == "effects.overlay_path":
            data["effects"]["overlay_path"] = str(found)
        else:
            data[entry.field] = str(found)
        fixed.append(f"{entry.label}: {found}")
    # порядок треков переезд не меняет: track_order хранит имена файлов
    # и уже лежит в data — from_dict вернёт его как есть
    return RenderJob.from_dict(data), fixed, still_missing
