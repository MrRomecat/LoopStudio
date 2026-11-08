"""Порядок треков плейлиста.

Правила (утверждены спецификацией):
1. Файлы с ЧИСТО числовыми именами (без расширения) идут первыми,
   по возрастанию числового значения: «2» < «03» < «10».
2. Остальные — после, по имени файла без учёта регистра, с натуральным
   сравнением цифровых участков («track 2» < «track 10»), как в проводнике.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

SUPPORTED_EXTENSIONS = frozenset({".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac"})

_NaturalKey = tuple[tuple[int, int | str], ...]


def natural_key(name: str) -> _NaturalKey:
    """Ключ натуральной сортировки: цифровые участки сравниваются как числа."""
    parts: list[tuple[int, int | str]] = []
    buffer = ""
    is_digit = False
    for char in name.lower():
        # isdecimal, не isdigit: для «²»/«①» isdigit истинен, а int() падает
        char_is_digit = char.isdecimal()
        if buffer and char_is_digit != is_digit:
            parts.append((0, int(buffer)) if is_digit else (1, buffer))
            buffer = ""
        buffer += char
        is_digit = char_is_digit
    if buffer:
        parts.append((0, int(buffer)) if is_digit else (1, buffer))
    return tuple(parts)


def _sort_key(path: Path) -> tuple[int, int | _NaturalKey, _NaturalKey]:
    stem = path.stem.strip()
    if stem.isdecimal():
        # чисто числовое имя: группа 0, по значению числа
        return (0, int(stem), natural_key(path.name))
    return (1, natural_key(stem), natural_key(path.name))


def sort_tracks(paths: Sequence[Path]) -> list[Path]:
    """Отсортировать треки по правилам плейлиста (исходный список не меняется)."""
    return sorted(paths, key=_sort_key)


def scan_music_folder(folder: Path) -> list[Path]:
    """Найти поддерживаемые аудиофайлы в папке и отсортировать их по правилам."""
    if not folder.is_dir():
        raise NotADirectoryError(f"Папка с музыкой не найдена: {folder}")
    tracks = [
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sort_tracks(tracks)
