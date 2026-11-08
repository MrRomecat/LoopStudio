"""Журнал очереди: снимки на диске, восстановление, мгновенная отмена pending."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

import core.render.queue as queue_module
from core.media.ffmpeg import FFmpegCancelled
from core.render.job import RenderJob
from core.render.journal import JournalEntry, QueueJournal
from core.render.queue import QueueItem, RenderQueue


def test_journal_roundtrip(tmp_path: Path) -> None:
    journal = QueueJournal(tmp_path / "queue_journal.json")
    entries = [
        JournalEntry(
            id=1,
            status="done",
            title="Ролик А",
            job=RenderJob(video_path="v.mp4").to_dict(),
            output_path="C:/out/a.mp4",
            warnings=["без быстрого старта"],
            elapsed_sec=12.5,
            created_at="2026-07-17T21:00:00",
        ),
        JournalEntry(id=2, status="running", title="Ролик Б"),
        JournalEntry(id=3, status="pending", title="Ролик В", preview=True),
    ]
    journal.write(entries)
    loaded = journal.load()
    assert [e.id for e in loaded] == [1, 2, 3]
    assert loaded[0].status == "done"
    assert loaded[0].warnings == ["без быстрого старта"]
    assert loaded[0].job["video_path"] == "v.mp4"
    assert not loaded[0].unfinished
    assert loaded[1].unfinished and loaded[2].unfinished
    assert loaded[2].preview is True
    # атомарность: временный файл не остался
    assert not list(tmp_path.glob("*.tmp"))


def test_journal_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "queue_journal.json"
    path.write_text("{битый json", encoding="utf-8")
    assert QueueJournal(path).load() == []
    path.write_text('{"schema_version": 1, "items": [42, {"id": 7}]}', encoding="utf-8")
    loaded = QueueJournal(path).load()
    assert len(loaded) == 1 and loaded[0].id == 7  # мусор пропущен, валидное живёт


def test_journal_clear(tmp_path: Path) -> None:
    journal = QueueJournal(tmp_path / "j.json")
    journal.write([JournalEntry(id=1)])
    assert journal.path.is_file()
    journal.clear()
    assert not journal.path.is_file()
    journal.clear()  # повторная очистка не падает


def test_queue_writes_journal_and_cancels_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Очередь пишет журнал на каждый статус; отмена pending видна мгновенно."""
    first_started = threading.Event()
    release_first = threading.Event()

    def fake_render(job: RenderJob, **kwargs: Any) -> object:
        first_started.set()
        release_first.wait(timeout=30)
        raise FFmpegCancelled("стоп")

    monkeypatch.setattr(queue_module, "render_job", fake_render)

    updates: list[tuple[int, str]] = []
    done = threading.Event()

    def on_update(item: QueueItem) -> None:
        updates.append((item.id, item.status))
        if item.id == 1 and item.status in ("done", "failed", "cancelled"):
            done.set()

    journal = QueueJournal(tmp_path / "queue_journal.json")
    q = RenderQueue(on_item_update=on_update, journal=journal)
    q.add(RenderJob(video_path="a.mp4"), title="Первый")
    q.add(RenderJob(video_path="b.mp4"), title="Второй")
    assert first_started.wait(timeout=10)

    q.cancel_item(2)  # ждущее — отменяется сразу, не дожидаясь воркера
    assert (2, "cancelled") in updates
    entries = {e.id: e for e in journal.load()}
    assert entries[2].status == "cancelled"
    assert entries[1].status == "running"
    assert entries[1].title == "Первый"
    assert entries[1].unfinished  # после «крэша» именно это задание предложат повторить

    # завершённое можно удалить из списка, выполняющееся — нельзя
    assert q.remove_item(2) is True
    assert q.remove_item(1) is False
    assert [e.id for e in journal.load()] == [1]

    release_first.set()
    assert done.wait(timeout=10)
    q.shutdown()
