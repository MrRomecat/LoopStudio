"""Управление питанием Windows: не дать системе уснуть при рендере,
выключение компьютера после завершения очереди.

ВАЖНО: SetThreadExecutionState действует на ВЫЗЫВАЮЩИЙ поток — все вызовы
:func:`keep_system_awake` обязаны идти из одного и того же потока (у нас —
главный поток Qt). Экран при этом гаснуть может (ES_DISPLAY_REQUIRED не
ставим) — не спит только система.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_CREATE_NO_WINDOW = 0x08000000


def keep_system_awake(enabled: bool) -> bool:
    """Запретить (или снова разрешить) системе засыпать. Возвращает успех.

    Закрытую крышку ноутбука это не перекрывает — политика крышки сильнее.
    """
    if sys.platform != "win32":
        return False
    flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if enabled else 0)
    try:
        result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except (AttributeError, OSError):
        return False
    return bool(result)


def _run_shutdown(args: list[str]) -> bool:
    """Запустить системную команду shutdown; True = код возврата 0."""
    if sys.platform != "win32":
        return False
    # абсолютный путь: не подхватить чужой shutdown.exe из папки приложения
    exe = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "shutdown.exe"
    try:
        # stdin=DEVNULL обязателен: в оконном exe наследуется невалидный хэндл
        result = subprocess.run(
            [str(exe), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    return result.returncode == 0


def schedule_shutdown(delay_sec: int = 30) -> bool:
    """Запланировать выключение компьютера через ``delay_sec`` секунд."""
    return _run_shutdown(["/s", "/t", str(max(0, delay_sec))])


def cancel_shutdown() -> bool:
    """Отменить запланированное выключение (``shutdown /a``)."""
    return _run_shutdown(["/a"])
