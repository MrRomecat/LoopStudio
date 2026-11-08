"""Журнал очереди рендера на диске: очередь переживает крэш и перезагрузку.

Снимок всех заданий пишется атомарно (tmp → replace) при каждом изменении
статуса. При старте приложение читает журнал прошлой сессии: незавершённые
задания (pending/running) можно поставить в очередь заново — с нуля,
возобновления с середины нет (это осознанно: перезапуск дешевле рисков
битой склейки).

Журнал — страховка, а не база данных: любая ошибка чтения/записи молча
игнорируется вызывающим кодом и никогда не роняет рендер.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.fsio import atomic_write_text

if TYPE_CHECKING:  # только для типов — без runtime-цикла с queue.py
    from core.render.queue import QueueItem

_SCHEMA_VERSION = 1


@dataclass(slots=True)
class JournalEntry:
    """Снимок одного задания очереди (для восстановления и «Итогов»)."""

    id: int
    preview: bool = False
    status: str = "pending"
    title: str = ""
    job: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    output_path: str = ""
    warnings: list[str] = field(default_factory=list)
    elapsed_sec: float | None = None
    created_at: str = ""

    @property
    def unfinished(self) -> bool:
        """Задание не доработало: рендер был прерван крэшем/перезагрузкой."""
        return self.status in ("pending", "running")


def entry_from_item(item: QueueItem, *, title: str = "") -> JournalEntry:
    """Собрать запись журнала из живого элемента очереди."""
    result = item.result
    output_path = str(result.output_path) if result is not None else ""
    warnings = list(result.warnings) if result is not None else []
    return JournalEntry(
        id=item.id,
        preview=item.preview,
        status=item.status,
        title=title,
        job=item.job.to_dict(),
        error=item.error,
        output_path=output_path,
        warnings=warnings,
        elapsed_sec=item.duration_seconds,
        created_at=item.created_at,
    )


class QueueJournal:
    """Файловый журнал очереди. Все методы бросают OSError — глотает вызывающий."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def write(self, entries: list[JournalEntry]) -> None:
        """Атомарно записать полный снимок очереди."""
        payload = {"schema_version": _SCHEMA_VERSION, "items": [asdict(entry) for entry in entries]}
        atomic_write_text(self._path, json.dumps(payload, ensure_ascii=False, indent=1))

    def load(self) -> list[JournalEntry]:
        """Прочитать журнал прошлой сессии. Битый/отсутствующий файл → пусто."""
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        entries: list[JournalEntry] = []
        known = JournalEntry.__dataclass_fields__
        for raw in data.get("items", []):
            if not isinstance(raw, dict):
                continue
            fields = {k: v for k, v in raw.items() if k in known}
            try:
                entries.append(JournalEntry(**fields))
            except TypeError:
                continue  # запись будущей/битой версии не роняет загрузку
        return entries

    def clear(self) -> None:
        """Удалить журнал (например, пользователь отказался от восстановления)."""
        self._path.unlink(missing_ok=True)
