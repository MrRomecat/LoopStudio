"""Атомарная запись файлов: временный файл рядом → fsync → ``os.replace``.

Закон продукта «пользователь никогда не теряет работу»: крэш или выключение
света посреди записи не должны уничтожать существующий файл. Паттерн один на
все хранимые файлы (настройки, проекты, журнал очереди, лицензия) — уникальное
имя временного файла заодно исключает столкновение двух писателей.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def _tmp_name(path: Path) -> Path:
    return path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Записать текст атомарно; при любом сбое прежний файл остаётся цел."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_name(path)
    try:
        with open(tmp, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """То же для бинарных данных (например, license.dat)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_name(path)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
