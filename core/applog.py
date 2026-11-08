"""Постоянный журнал приложения: %APPDATA%/LoopStudio/logs/loopstudio.log.

Раньше след на диске оставляли только сбои ffmpeg — DiskSpaceError, ошибки
проб и неожиданные баги ночью исчезали вместе с окном, и в поддержку было
нечего отправить. Теперь: старт/финиш приложения и заданий, все ошибки и
предупреждения — в ротируемом файле (3 × 1 МБ). Любая ночная проблема
воспроизводима одним файлом.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import sys
import traceback
from pathlib import Path
from types import TracebackType

from core.settings import settings_dir

_LOGGER_NAME = "loopstudio"
_MAX_BYTES = 1_000_000
_BACKUPS = 3

log = logging.getLogger(_LOGGER_NAME)


def log_dir() -> Path:
    return settings_dir() / "logs"


def log_file() -> Path:
    return log_dir() / "loopstudio.log"


def setup_logging(app_version: str) -> None:
    """Включить журнал (вызывается один раз при старте приложения)."""
    if log.handlers:
        return  # уже настроен (повторный вызов в тестах)
    log.setLevel(logging.INFO)
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
    except OSError:
        return  # журнал — сервис: без него приложение всё равно работает
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    log.addHandler(handler)
    log.info("=== LoopStudio %s запущен ===", app_version)


def install_excepthook(on_error: object | None = None) -> None:
    """Необработанные исключения → журнал (приложение не молчит о падениях).

    :param on_error: колбэк ``(текст) -> None`` для показа пользователю
        (вызывается в дополнение к записи в журнал).
    """
    previous = sys.excepthook

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Необработанная ошибка:\n%s", text)
        if callable(on_error):
            with contextlib.suppress(Exception):  # показ ошибки не должен падать сам
                on_error(str(exc))
        previous(exc_type, exc, tb)

    sys.excepthook = hook
