"""Очередь рендера: задания выполняются последовательно в фоновом потоке.

Killer-фича серийного производства: настроил 5 роликов → ушёл → утром готово.
UI подписывается колбэками; движок ничего не знает про Qt.

Надёжность ночной очереди: каждый переход статуса пишется в журнал на диске
(:mod:`core.render.journal`) — после крэша или перезагрузки Windows приложение
видит, что было в очереди, и предлагает перезапустить незавершённое.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from core.applog import log
from core.errors import humanize_error
from core.media.ffmpeg import FFmpegCancelled
from core.render.job import RenderJob
from core.render.journal import QueueJournal, entry_from_item
from core.render.pipeline import RenderResult, render_job
from core.render.progress import ProgressUpdate

ItemStatus = Literal["pending", "running", "done", "failed", "cancelled"]

#: статусы, в которых задание уже не выполняется и не будет
TERMINAL_STATUSES: tuple[ItemStatus, ...] = ("done", "failed", "cancelled")


@dataclass(slots=True)
class QueueItem:
    """Одно задание в очереди."""

    id: int
    job: RenderJob
    preview: bool = False
    preview_from_start: bool = False  # «Начало ролика»: без случайного смещения
    status: ItemStatus = "pending"
    title: str = ""  # человекочитаемое имя для строк очереди/журнала/итогов
    result: RenderResult | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    started_at: float | None = None  # time.monotonic()
    finished_at: float | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def duration_seconds(self) -> float | None:
        """Сколько заняло выполнение (для «Готово за 10:23»)."""
        if self.started_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.started_at


OnItemUpdate = Callable[[QueueItem], None]
OnItemProgress = Callable[[QueueItem, ProgressUpdate], None]


class RenderQueue:
    """Последовательная очередь рендера. Колбэки приходят из фонового потока —
    UI-слой обязан переправить их в главный поток (Qt: сигналы)."""

    def __init__(
        self,
        on_item_update: OnItemUpdate | None = None,
        on_item_progress: OnItemProgress | None = None,
        journal: QueueJournal | None = None,
    ) -> None:
        self._on_item_update = on_item_update
        self._on_item_progress = on_item_progress
        self._journal = journal
        self._queue: queue.Queue[QueueItem | None] = queue.Queue()
        self._items: list[QueueItem] = []
        self._lock = threading.Lock()
        self._next_id = 1
        self._worker = threading.Thread(target=self._run, daemon=True, name="render-queue")
        self._worker.start()

    @property
    def items(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    @property
    def busy(self) -> bool:
        """Есть ли задания, которые выполняются или ждут выполнения."""
        with self._lock:
            return any(item.status in ("pending", "running") for item in self._items)

    def add(
        self,
        job: RenderJob,
        *,
        preview: bool = False,
        preview_from_start: bool = False,
        title: str = "",
    ) -> QueueItem:
        with self._lock:
            item = QueueItem(
                id=self._next_id,
                job=job,
                preview=preview,
                preview_from_start=preview_from_start,
                title=title,
            )
            self._next_id += 1
            self._items.append(item)
        self._notify(item)
        self._queue.put(item)
        return item

    def cancel_item(self, item_id: int) -> None:
        """Отменить задание: ждущее — снимается сразу, выполняющееся — прерывается."""
        notify: QueueItem | None = None
        with self._lock:
            for item in self._items:
                if item.id == item_id and item.status in ("pending", "running"):
                    item.cancel.set()
                    if item.status == "pending":
                        # ждущее умирает мгновенно — UI видит «отменено» сразу,
                        # а не когда до задания дойдёт воркер
                        item.status = "cancelled"
                        notify = item
                    break
        if notify is not None:
            self._notify(notify)

    def remove_item(self, item_id: int) -> bool:
        """Убрать завершённое задание из списка (готово/ошибка/отменено)."""
        removed = False
        with self._lock:
            for item in self._items:
                if item.id == item_id and item.status in TERMINAL_STATUSES:
                    self._items.remove(item)
                    removed = True
                    break
        if removed:
            self._write_journal()
        return removed

    def shutdown(self) -> None:
        """Отменить всё и остановить поток (вызывается при выходе из приложения).

        Ждущие задания сразу помечаются «отменено» — иначе они остались бы
        в журнале как pending и после осознанного закрытия приложение ложно
        предложило бы «восстановить прерванную очередь».
        """
        to_notify: list[QueueItem] = []
        with self._lock:
            for item in self._items:
                if item.status == "pending":
                    item.cancel.set()
                    item.status = "cancelled"
                    to_notify.append(item)
                elif item.status == "running":
                    item.cancel.set()
        for item in to_notify:
            self._notify(item)
        self._queue.put(None)
        self._worker.join(timeout=10)

    def _notify(self, item: QueueItem) -> None:
        self._write_journal()
        if self._on_item_update is not None:
            self._on_item_update(item)

    def detach_journal(self) -> QueueJournal | None:
        """Отвязать журнал от очереди (перед записью «продолжить при следующем
        запуске»): воркер, переживший таймаут shutdown, уже не затрёт снимок."""
        with self._lock:
            journal, self._journal = self._journal, None
            return journal

    def _write_journal(self) -> None:
        """Снимок очереди на диск. Журнал — страховка: сбой записи не роняет рендер.

        Запись держится под локом: воркер и UI-поток пишут журнал конкурентно,
        без сериализации устаревший снимок мог перезаписать более новый.
        """
        with self._lock:
            if self._journal is None:
                return
            entries = [entry_from_item(item, title=item.title) for item in self._items]
            with contextlib.suppress(OSError):
                self._journal.write(entries)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if item.cancel.is_set():
                if item.status != "cancelled":
                    item.status = "cancelled"
                    self._notify(item)
                continue
            item.status = "running"
            item.started_at = time.monotonic()
            log.info("Задание №%d «%s»: старт рендера", item.id, item.title)
            self._notify(item)

            def on_progress(update: ProgressUpdate, current: QueueItem = item) -> None:
                if self._on_item_progress is not None:
                    self._on_item_progress(current, update)

            try:
                item.result = render_job(
                    item.job,
                    preview=item.preview,
                    preview_from_start=item.preview_from_start,
                    on_progress=on_progress,
                    cancel=item.cancel,
                )
                item.status = "done"
                log.info(
                    "Задание №%d: готово (%s)",
                    item.id,
                    item.result.output_path if item.result else "",
                )
            except FFmpegCancelled:
                item.status = "cancelled"
                log.info("Задание №%d: отменено пользователем", item.id)
            except Exception as exc:  # очередь не должна умирать от любой ошибки задания
                item.status = "failed"
                item.error = humanize_error(exc)
                # полный traceback — в журнал: ночной сбой воспроизводим одним файлом
                log.exception("Задание №%d: ошибка рендера", item.id)
            item.finished_at = time.monotonic()
            self._notify(item)
