"""Этап 4: очередь рендера — последовательное выполнение, статусы, отмена."""

from __future__ import annotations

import threading
from pathlib import Path

from core.render.job import RenderJob
from core.render.queue import QueueItem, RenderQueue


def _job(video: Path, music: Path, out: Path) -> RenderJob:
    return RenderJob(
        video_path=str(video),
        music_dir=str(music),
        output_dir=str(out),
        duration_sec=60.0,
        resolution="720p",
        quality="standard",
    )


def test_queue_runs_and_cancels(video_5s: Path, music_dir: Path, tmp_path: Path) -> None:
    done = threading.Event()
    seen: list[tuple[int, str]] = []

    def on_update(item: QueueItem) -> None:
        seen.append((item.id, item.status))
        if item.status in ("done", "failed", "cancelled") and item.id == 1:
            done.set()

    q = RenderQueue(on_item_update=on_update)
    first = q.add(_job(video_5s, music_dir, tmp_path / "a"), preview=True)
    second = q.add(_job(video_5s, music_dir, tmp_path / "b"), preview=True)
    q.cancel_item(second.id)  # ждущее задание снимается мгновенно, не в порядке очереди
    assert second.status == "cancelled"
    assert (2, "cancelled") in seen

    assert done.wait(timeout=180), "очередь не завершилась за разумное время"
    q.shutdown()

    assert first.status == "done"
    assert first.result is not None and first.result.output_path.is_file()
    assert first.result.duration >= 60.0, "резиновый таймлайн: не короче заданного"
    assert second.status == "cancelled"
    assert (1, "running") in seen and (1, "done") in seen
