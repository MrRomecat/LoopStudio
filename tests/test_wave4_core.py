"""Волна 4: человеческие ошибки, журнал, резолв встроенных ассетов."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.errors import humanize_error


def test_humanize_winerror_32() -> None:
    exc = OSError(None, "The process cannot access the file", None, 32)
    text = humanize_error(exc)
    assert "занят другой программой" in text
    assert "The process cannot access the file" in text  # деталь сохранена


def test_humanize_keeps_russian_domain_errors() -> None:
    from core.render.job import JobError

    exc = JobError("Не выбрано основное видео (шаг 1).")
    assert humanize_error(exc) == "Не выбрано основное видео (шаг 1)."


def test_humanize_unknown_exception_wrapped() -> None:
    text = humanize_error(ValueError("boom"))
    assert "Внутренняя ошибка" in text
    assert "boom" in text


def test_queue_failure_is_humanized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сырая WinError из рендера доходит до пользователя человеческим текстом."""
    import threading

    import core.render.queue as queue_module
    from core.render.job import RenderJob
    from core.render.queue import QueueItem, RenderQueue

    def boom(*args: object, **kwargs: object) -> object:
        raise OSError(None, "The process cannot access the file", None, 32)

    monkeypatch.setattr(queue_module, "render_job", boom)
    done = threading.Event()
    failed: list[QueueItem] = []

    def on_update(item: QueueItem) -> None:
        if item.status == "failed":
            failed.append(item)
            done.set()

    q = RenderQueue(on_item_update=on_update)
    q.add(RenderJob(video_path="x.mp4"), title="Тест")
    assert done.wait(timeout=10)
    q.shutdown()
    assert failed and failed[0].error is not None
    assert "занят другой программой" in failed[0].error


def test_builtin_assets_present() -> None:
    """Встроенная библиотека установлена и полна (сгенерирована в репо)."""
    from core.assets import (
        demo_music_dir,
        factory_presets,
        list_ambients,
        list_demo_backgrounds,
        list_overlays,
    )

    assert len(list_ambients()) >= 8
    assert len(list_overlays()) >= 5
    assert len(list_demo_backgrounds()) >= 2
    assert demo_music_dir() is not None
    # заводские пресеты собрались и указывают на существующие файлы
    presets = factory_presets()
    assert len(presets) >= 3
    for data in presets.values():
        for layer in data["ambient"]:
            if layer["enabled"]:
                assert Path(layer["path"]).is_file()


def test_factory_presets_resolve_when_assets_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет ассетов → нет заводских пресетов (не падаем, не тащим битые пути)."""
    import core.assets as assets_mod

    monkeypatch.setattr(assets_mod, "list_ambients", list)
    assert assets_mod.factory_presets() == {}


def test_applog_writes_and_rotates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.applog as applog

    monkeypatch.setattr("core.applog.settings_dir", lambda: tmp_path)
    # свежий логгер без хендлеров для изолированного теста
    for handler in list(applog.log.handlers):
        applog.log.removeHandler(handler)
    applog.setup_logging("9.9.9-тест")
    applog.log.info("тестовая запись")
    for handler in applog.log.handlers:
        handler.flush()
    content = applog.log_file().read_text(encoding="utf-8")
    assert "LoopStudio 9.9.9-тест запущен" in content
    assert "тестовая запись" in content
