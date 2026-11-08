"""Настройки приложения: последние папки, пресеты микса.

Хранятся в ``%APPDATA%/LoopStudio/settings.json`` — переживают переустановку.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.fsio import atomic_write_text

log = logging.getLogger(__name__)


def settings_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "LoopStudio"


@dataclass(slots=True)
class AppSettings:
    """Пользовательские настройки (не конфигурация ролика — та живёт в .lsproj)."""

    last_output_dir: str = ""
    last_project_dir: str = ""
    last_music_dir: str = ""
    mix_presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: профиль канала = снимок всей формы, кроме папки музыки и длительности
    channel_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: последние открытые/сохранённые .lsproj (новые — в начале, максимум 10)
    recent_projects: list[str] = field(default_factory=list)

    def remember_project(self, path: str) -> None:
        """Поднять проект в начало списка недавних (без дублей, максимум 10)."""
        items = [p for p in self.recent_projects if p != path]
        items.insert(0, path)
        del items[10:]
        self.recent_projects = items

    @classmethod
    def load(cls) -> AppSettings:
        file = settings_dir() / "settings.json"
        if not file.is_file():
            return cls()
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict):
            # битые настройки не роняют приложение; файл сохраняем для разбора
            with contextlib.suppress(OSError):
                file.replace(file.with_name("settings.json.corrupt"))
            log.warning("settings.json повреждён — стартую с настроек по умолчанию")
            return cls()
        defaults = cls()
        clean: dict[str, Any] = {}
        for name, value in data.items():
            if name not in cls.__dataclass_fields__:
                continue  # ключи будущих версий не мешают
            if isinstance(value, type(getattr(defaults, name))):
                clean[name] = value  # поле неверного типа → значение по умолчанию
        return cls(**clean)

    def save(self) -> None:
        """Сохранить настройки. Сбой диска не роняет приложение — данные живы в памяти."""
        try:
            atomic_write_text(
                settings_dir() / "settings.json",
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
            )
        except OSError:
            log.warning("Не удалось сохранить settings.json", exc_info=True)
