"""Встроенная библиотека ассетов: резолв путей и заводские пресеты.

Ассеты лежат в ``assets/`` рядом с приложением (dev — корень репозитория,
сборка — папка exe: PyInstaller пакует assets туда же). Пути всегда
резолвятся на лету от :func:`core.media.ffmpeg.app_root` — переустановка
в другую папку ничего не ломает.

Весь контент сгенерирован процедурно (scripts/generate_assets.py) —
лицензия свободная, подробности в assets/LICENSES.txt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.media.ffmpeg import app_root

#: пометка заводских пресетов в списке (их нельзя удалить или перезаписать)
FACTORY_PREFIX = "★ "

LICENSE_NOTE = (
    "Все встроенные звуки и эффекты сгенерированы самим LoopStudio — "
    "использование свободное, без указания авторства (см. LICENSES.txt)."
)


@dataclass(frozen=True, slots=True)
class BuiltinAsset:
    """Один встроенный файл: имя для списка + путь на диске."""

    name: str
    path: Path


def assets_root() -> Path:
    return app_root() / "assets"


def _list_files(folder: Path, suffixes: tuple[str, ...]) -> list[BuiltinAsset]:
    if not folder.is_dir():
        return []
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in suffixes]
    return [BuiltinAsset(name=p.stem, path=p) for p in files]


def list_ambients() -> list[BuiltinAsset]:
    """Встроенные эмбиент-лупы (шаг 3)."""
    return _list_files(assets_root() / "ambient", (".flac", ".wav", ".mp3"))


def list_overlays() -> list[BuiltinAsset]:
    """Встроенные VFX-оверлеи (шаг 4)."""
    return _list_files(assets_root() / "overlays", (".webm", ".mp4"))


def list_demo_backgrounds() -> list[BuiltinAsset]:
    """Демо-видеофоны для мастера первого запуска."""
    return _list_files(assets_root() / "demo", (".mp4", ".mov"))


def demo_music_dir() -> Path | None:
    """Папка демо-треков (None, если демо не установлено)."""
    folder = assets_root() / "demo" / "music"
    return folder if folder.is_dir() and any(folder.iterdir()) else None


def _find_ambient(name_part: str) -> Path | None:
    for asset in list_ambients():
        if name_part.lower() in asset.name.lower():
            return asset.path
    return None


#: заводские пресеты микса: имя → слои (фрагмент имени ассета, громкость)
_FACTORY_RECIPES: dict[str, list[tuple[str, float]]] = {
    "Дождливый вечер": [("Дождь", 0.45)],
    "Гроза за окном": [("Ливень", 0.5)],
    "Океанский берег": [("Океан", 0.5)],
    "Камин и метель": [("Костёр", 0.45), ("Ветер", 0.25)],
    "Ночь у костра": [("Костёр", 0.4), ("Сверчки", 0.3)],
}


def factory_presets() -> dict[str, dict[str, Any]]:
    """Заводские пресеты микса с путями, разрешёнными на лету.

    Формат совпадает с пользовательскими ``AppSettings.mix_presets``.
    Пресет попадает в список, только если все его звуки установлены.
    """
    presets: dict[str, dict[str, Any]] = {}
    for name, layers in _FACTORY_RECIPES.items():
        resolved: list[dict[str, Any]] = []
        for name_part, volume in layers:
            path = _find_ambient(name_part)
            if path is None:
                break
            resolved.append({"enabled": True, "path": str(path), "volume": volume})
        else:
            while len(resolved) < 3:
                resolved.append({"enabled": False, "path": "", "volume": 0.5})
            presets[f"{FACTORY_PREFIX}{name}"] = {"music_volume": 1.0, "ambient": resolved}
    return presets
