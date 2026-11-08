"""LoopStudio как модуль Studio Suite (контракт SuiteModule)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from core.render.job import RenderJob
from core.suite import AppContext
from modules.loopstudio.controller import LoopStudioController
from modules.loopstudio.screen import LoopStudioScreen


class LoopStudioWidget(LoopStudioScreen):
    """Экран + контроллер одним виджетом (контроллер живёт, пока жив экран)."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx.settings)
        self.controller = LoopStudioController(self, ctx.settings)


class LoopStudioModule:
    """Модуль 1: длинные музыкальные видео."""

    id = "loopstudio"
    title = "Длинные музыкальные видео"

    def create_widget(self, ctx: AppContext) -> QWidget:
        return LoopStudioWidget(ctx)

    def create_job(self, config: dict[str, Any]) -> RenderJob:
        return RenderJob.from_dict(config)
