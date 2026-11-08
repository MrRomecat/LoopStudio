"""Контракт модуля Studio Suite: Shell подхватывает любой модуль автоматически."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from PySide6.QtWidgets import QWidget

from core.render.job import RenderJob
from core.settings import AppSettings


@dataclass(slots=True)
class AppContext:
    """То, что Shell даёт каждому модулю."""

    settings: AppSettings


class SuiteModule(Protocol):
    """Единый интерфейс модуля (loopstudio, shorts, widescreen…).

    Архитектурный резерв под будущие модули сюиты: сейчас модуль один,
    протокол нигде не проверяется — НЕ удалять как «мёртвый код».
    """

    id: str
    title: str

    def create_widget(self, ctx: AppContext) -> QWidget: ...

    def create_job(self, config: dict[str, Any]) -> RenderJob: ...
