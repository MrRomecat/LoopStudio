"""Поиск и запуск внешних бинарников ffmpeg/ffprobe.

Порядок поиска бинарника:
1. Папка ``bin/`` рядом с корнем приложения (поставляется с дистрибутивом).
2. Системный PATH (удобно для разработки).

Запуск ffmpeg — через :func:`run_ffmpeg`: прогресс читается из машиночитаемого
потока ``-progress pipe:1`` (надёжнее, чем регэксп по stderr), stderr копится
для человекочитаемой ошибки, отмена — через :class:`threading.Event`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO

ProgressCallback = Callable[[float], None]
"""Колбэк прогресса: сколько секунд выходного медиа уже обработано."""

_STDERR_TAIL_LINES = 30
_CREATE_NO_WINDOW = 0x08000000  # не открывать консольное окно на Windows


class FFmpegNotFoundError(RuntimeError):
    """ffmpeg/ffprobe не найден ни в bin/, ни в PATH."""


class FFmpegError(RuntimeError):
    """ffmpeg завершился с ошибкой; содержит хвост stderr и команду для лога."""

    def __init__(self, message: str, stderr_tail: str, command: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail
        self.command = command


class FFmpegCancelled(Exception):
    """Операция отменена пользователем."""


def app_root() -> Path:
    """Корень приложения: папка репозитория (dev) или папка exe (PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def find_binary(name: str) -> Path:
    """Найти бинарник по имени (без расширения), например ``ffmpeg``."""
    exe = f"{name}.exe" if sys.platform == "win32" else name
    bundled = app_root() / "bin" / exe
    if bundled.is_file():
        return bundled
    on_path = shutil.which(name)
    if on_path is not None:
        return Path(on_path)
    raise FFmpegNotFoundError(
        f"Не найден {name}: положите {exe} в папку bin/ "
        f"(запустите scripts/fetch_ffmpeg.py) или добавьте его в PATH."
    )


def find_ffmpeg() -> Path:
    """Путь к ffmpeg."""
    return find_binary("ffmpeg")


def find_ffprobe() -> Path:
    """Путь к ffprobe."""
    return find_binary("ffprobe")


def _parse_out_time(key: str, value: str) -> float | None:
    """Строка ``-progress`` → секунды. Понимает out_time_us/out_time_ms/out_time."""
    if key in ("out_time_us", "out_time_ms"):
        # out_time_ms исторически тоже в микросекундах (баг-легаси ffmpeg)
        try:
            return max(0.0, int(value) / 1_000_000)
        except ValueError:
            return None
    if key == "out_time":
        try:
            hh, mm, ss = value.split(":")
            return max(0.0, int(hh) * 3600 + int(mm) * 60 + float(ss))
        except ValueError:
            return None
    return None


def run_ffmpeg(
    args: Sequence[str],
    *,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Выполнить ffmpeg с заданными аргументами (без самого имени бинарника).

    Флаги ``-y -hide_banner -nostdin -loglevel error -progress pipe:1``
    добавляются автоматически.

    :raises FFmpegError: ненулевой код выхода (сообщение + хвост stderr).
    :raises FFmpegCancelled: сработал ``cancel``.
    """
    cmd = [
        str(find_ffmpeg()),
        "-y",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        *args,
    ]
    creationflags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,  # в оконном exe (PyInstaller) нет консоли — без
        stdout=subprocess.PIPE,  # DEVNULL наследуется невалидный хэндл и запуск падает
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stderr_lines: list[str] = []

    def _drain_stderr(stream: IO[str]) -> None:
        for line in iter(stream.readline, ""):
            stderr_lines.append(line.rstrip())

    stderr_thread = threading.Thread(target=_drain_stderr, args=(process.stderr,), daemon=True)
    stderr_thread.start()

    cancelled = False
    try:
        for raw in process.stdout:
            if cancel is not None and cancel.is_set():
                cancelled = True
                process.kill()
                break
            key, _, value = raw.strip().partition("=")
            seconds = _parse_out_time(key, value)
            if seconds is not None and on_progress is not None:
                on_progress(seconds)
    finally:
        process.stdout.close()
        returncode = process.wait()
        stderr_thread.join(timeout=5)

    if cancelled:
        raise FFmpegCancelled("Операция отменена пользователем.")
    if returncode != 0:
        tail = "\n".join(stderr_lines[-_STDERR_TAIL_LINES:])
        raise FFmpegError(
            f"ffmpeg завершился с ошибкой (код {returncode}). Подробности — в логе.",
            stderr_tail=tail,
            command=" ".join(cmd),
        )
