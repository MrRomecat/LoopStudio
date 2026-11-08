"""Контроллер LoopStudio: связывает экран с движком рендера.

Правила: UI-поток никогда не блокируется; колбэки очереди приходят из
фонового потока и переправляются в главный через сигналы Qt (queued).
Управление питанием (keep-awake) вызывается только из главного потока —
SetThreadExecutionState действует на вызывающий поток.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from core import power
from core.assets import FACTORY_PREFIX, factory_presets, list_ambients, list_overlays
from core.audio.analyze import AnalyzeError, TrackAnalysis, analyze_track
from core.audio.mixdown import AmbientLayer, MixdownSettings, build_master
from core.audio.shuffle import smart_shuffle
from core.audio.timeline import TimelineError, build_timeline
from core.media.ffmpeg import FFmpegCancelled, FFmpegError, app_root
from core.media.probe import MediaInfo, ProbeError, probe
from core.render.batch import clone_job_for_folder
from core.render.job import SCHEMA_VERSION, JobError, RenderJob
from core.render.journal import JournalEntry, QueueJournal, entry_from_item
from core.render.pipeline import _human_duration, _ordered_tracks
from core.render.preflight import PreflightReport, run_preflight
from core.render.progress import ProgressUpdate
from core.render.project_check import check_project, relink_job
from core.render.queue import TERMINAL_STATUSES, QueueItem, RenderQueue
from core.render.seams import build_seams_preview
from core.settings import AppSettings, settings_dir
from modules.loopstudio.dialogs import (
    AssetGalleryDialog,
    BatchDialog,
    ContinueSessionDialog,
    NameDialog,
    PreflightDialog,
    ProjectIssuesDialog,
    QueueSummaryDialog,
    QuickSetupDialog,
    ShutdownCountdownDialog,
)
from modules.loopstudio.screen import (
    QUEUE_ROLE_BADGE,
    QUEUE_ROLE_META,
    QUEUE_ROLE_STATUS,
    QUEUE_ROLE_TITLE,
    AmbientRow,
    LoopStudioScreen,
)

log = logging.getLogger(__name__)

_MIX_PREVIEW_SECONDS = 30.0
_SHUTDOWN_COUNTDOWN_SEC = 60
_SHUTDOWN_GRACE_SEC = 60  # запас после «Выключить»: успеть отменить
_CREATE_NO_WINDOW = 0x08000000
_AUTOSAVE_INTERVAL_MS = 45_000
#: профиль канала — вся форма, КРОМЕ того, что меняется от ролика к ролику
_PROFILE_EXCLUDED_FIELDS = (
    "music_dir",
    "track_order",
    "excluded_tracks",  # привязаны к конкретной папке музыки
    "duration_sec",
    "schema_version",
)

_STATUS_LABELS = {
    "pending": "в очереди",
    "running": "выполняется…",
    "done": "готово",
    "failed": "ошибка",
    "cancelled": "отменено",
}


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _hms(seconds: float) -> str:
    total = round(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


class _Bridge(QObject):
    """Сигналы из фоновых потоков в главный."""

    item_updated = Signal(object)
    item_progress = Signal(object, object)
    mix_ready = Signal(str)
    mix_failed = Signal(str)
    preflight_done = Signal(object, object)  # (RenderJob, PreflightReport)
    video_probed = Signal(str, object)  # (путь, MediaInfo | ProbeError)
    shuffle_done = Signal(object)  # list[str] — новый порядок треков
    lint_progress = Signal(str)
    lint_done = Signal(object)  # dict[str, TrackAnalysis] по имени файла
    seams_ready = Signal(str)
    seams_failed = Signal(str)


class LoopStudioController(QObject):
    """Оркестровка экрана: очередь, пре-флайт, предпрослушка, пресеты, проекты."""

    #: суффикс заголовка окна: « — Имя проекта *» (пусто = без проекта)
    project_state_changed = Signal(str)

    def __init__(self, screen: LoopStudioScreen, settings: AppSettings) -> None:
        super().__init__(screen)
        self._screen = screen
        self._settings = settings
        self._bridge = _Bridge()
        self._journal = QueueJournal(settings_dir() / "queue_journal.json")
        self._restore_entries = self._journal.load()  # читаем ДО первой записи очереди
        self._queue = RenderQueue(
            on_item_update=self._bridge.item_updated.emit,
            on_item_progress=self._bridge.item_progress.emit,
            journal=self._journal,
        )
        self._last_error_log: Path | None = None
        self._last_progress_at: float | None = None
        self._last_progress_text = ""
        self._pulse = QTimer(self)
        self._pulse.setInterval(2000)
        self._pulse.timeout.connect(self._on_pulse)
        self._pulse.start()
        self._mix_thread: threading.Thread | None = None
        self._preflight_thread: threading.Thread | None = None
        self._shuffle_thread: threading.Thread | None = None
        self._lint_thread: threading.Thread | None = None
        self._lint_results: dict[str, TrackAnalysis] = {}
        self._seams_thread: threading.Thread | None = None
        self._seams_cancel = threading.Event()
        self._probe_cache: dict[tuple[str, float, int], MediaInfo] = {}
        self._probe_cache_lock = threading.Lock()
        self._mix_preview_file: Path | None = None
        self._was_busy = False
        self._counts_at_start = (0, 0)
        self._preserve_entries: list[JournalEntry] = []
        self._batch_children: list[RenderJob] = []
        self._batch_folders: list[Path] = []  # выбранный пакет папок (шаг 2)
        self._session_file = settings_dir() / "last_session.lsproj"
        self._last_session_snapshot: dict[str, Any] | None = None
        self._current_project: Path | None = None
        self._saved_snapshot: dict[str, Any] | None = None
        self._autosave = QTimer(self)
        self._autosave.setInterval(_AUTOSAVE_INTERVAL_MS)
        self._autosave.timeout.connect(self._autosave_tick)
        # старт — после диалогов восстановления: тик не должен перезаписать
        # снимок прошлого сеанса, пока пользователь решает, вернуть ли его
        self._shutdown_dialog: ShutdownCountdownDialog | None = None
        self._tray = self._make_tray()
        self._audio_out = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_out)
        self._playing_path = ""

        self._bridge.item_updated.connect(self._on_item_updated)
        self._bridge.item_progress.connect(self._on_item_progress)
        self._bridge.mix_ready.connect(self._on_mix_ready)
        self._bridge.mix_failed.connect(self._on_mix_failed)
        self._bridge.preflight_done.connect(self._on_preflight_done)
        self._bridge.video_probed.connect(self._on_video_probed)
        self._bridge.shuffle_done.connect(self._on_shuffle_done)
        self._bridge.lint_progress.connect(self._on_lint_progress)
        self._bridge.lint_done.connect(self._on_lint_done)
        self._bridge.seams_ready.connect(self._on_seams_ready)
        self._bridge.seams_failed.connect(self._on_seams_failed)

        screen.generate_clicked.connect(self._on_generate)
        screen.preview_clicked.connect(self._on_preview)
        screen.open_folder_clicked.connect(self._on_open_folder)
        screen.cancel_clicked.connect(self._on_cancel)
        screen.listen_mix_clicked.connect(self._on_listen_mix)
        screen.ambient_play_requested.connect(self._on_play_file)
        screen.save_mix_preset_clicked.connect(self._on_save_preset)
        screen.delete_mix_preset_clicked.connect(self._on_delete_preset)
        screen.mix_preset_selected.connect(self._on_apply_preset)
        screen.output_picker.pathChanged.connect(self._remember_output_dir)
        screen.open_log_btn.clicked.connect(self._open_error_log)
        screen.summary_btn.clicked.connect(self._show_summary)
        screen.video_picker.pathChanged.connect(self._on_video_path_changed)
        screen.queue_list.customContextMenuRequested.connect(self._queue_menu)
        screen.save_profile_clicked.connect(self._on_save_profile)
        screen.delete_profile_clicked.connect(self._on_delete_profile)
        screen.profile_selected.connect(self._on_apply_profile)
        screen.quick_setup_clicked.connect(self.run_quick_setup)
        screen.batch_clicked.connect(self._on_batch)
        screen.batch_clear_clicked.connect(self._on_batch_clear)
        screen.ambient_builtin_requested.connect(self._on_ambient_builtin)
        screen.overlay_builtin_clicked.connect(self._on_overlay_builtin)
        screen.shuffle_clicked.connect(self._on_shuffle)
        screen.lint_clicked.connect(self._on_lint)
        screen.stop_mix_clicked.connect(self._on_stop_playback)
        screen.preview_start_clicked.connect(self._on_preview_start)
        screen.seams_clicked.connect(self._on_seams)
        self._refresh_presets()
        self._refresh_profiles()
        # диалоги восстановления — после того как окно показано
        QTimer.singleShot(0, self._offer_startup_restore)

    # --- жизненный цикл -------------------------------------------------------

    def request_close(self) -> bool:
        """Спросить пользователя, если очередь ещё работает. False = не закрывать.

        «Продолжить при следующем запуске» сохраняет незавершённые задания
        в журнал как есть — при следующем открытии приложение предложит
        поставить их заново (прерванный рендер начнётся с начала).
        """
        active = [i for i in self._queue.items if i.status in ("pending", "running")]
        if not active:
            return True
        box = QMessageBox(self._screen.window())
        box.setWindowTitle("LoopStudio — очередь ещё работает")
        box.setText(
            f"Незавершённых заданий: {len(active)}.\n\n"
            "«Продолжить при следующем запуске» — задания сохранятся, и при "
            "следующем открытии LoopStudio предложит поставить их заново\n"
            "(прерванный рендер начнётся с начала)."
        )
        keep_btn = box.addButton(
            "Закрыть, продолжить при следующем запуске", QMessageBox.ButtonRole.AcceptRole
        )
        drop_btn = box.addButton(
            "Закрыть и отменить задания", QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton("Не закрывать", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep_btn:
            # снимок ДО остановки очереди: после неё статусы станут «отменено»
            self._preserve_entries = [
                entry_from_item(i, title=i.title) for i in active if not i.preview
            ]
            return True
        return clicked is drop_btn

    def shutdown(self) -> None:
        self._player.stop()
        power.keep_system_awake(False)
        if self._tray is not None:
            self._tray.hide()
        with contextlib.suppress(OSError):  # финальный снимок формы
            self._screen.collect_job().save(self._session_file)
        self._queue.shutdown()
        # отвязать журнал ДО финальной записи: воркер, переживший таймаут
        # остановки, уже не затрёт снимок «продолжить при следующем запуске»
        journal = self._queue.detach_journal() or self._journal
        if self._preserve_entries:
            with contextlib.suppress(OSError):
                journal.write(self._preserve_entries)
        self._settings.save()

    # --- трей и уведомления ---------------------------------------------------

    def _make_tray(self) -> QSystemTrayIcon | None:
        for name in ("icon_256.png", "icon.ico"):
            icon_path = app_root() / "assets" / name
            if icon_path.is_file():
                tray = QSystemTrayIcon(QIcon(str(icon_path)), self)
                tray.setToolTip("LoopStudio — очередь рендера")
                tray.show()
                return tray
        return None

    def _notify_user(self, title: str, message: str) -> None:
        """Системное уведомление Windows (если трей доступен)."""
        if self._tray is not None and QSystemTrayIcon.supportsMessages():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 10_000)

    # --- автоснимок формы и восстановление при старте ---------------------------

    def _offer_startup_restore(self) -> None:
        self._offer_form_restore()
        self._offer_queue_restore()
        self._autosave.start()

    def _offer_form_restore(self) -> None:
        """«Продолжить, где остановились?» — вернуть форму прошлого сеанса."""
        if not self._session_file.is_file():
            return
        try:
            job = RenderJob.load(self._session_file)
        except JobError:
            log.warning("Снимок прошлого сеанса повреждён — восстановление недоступно")
            return
        # сравниваем со СВЕЖЕЙ формой (в ней уже предзаполнена папка сохранения):
        # иначе вопрос выскакивал бы при каждом запуске без реальных отличий
        if job.to_dict() in (RenderJob().to_dict(), self._screen.collect_job().to_dict()):
            return  # прошлый сеанс не отличается от свежей формы — нечего возвращать
        if ContinueSessionDialog(self._screen).exec() == QDialog.DialogCode.Accepted:
            self._screen.apply_job(job)
        else:
            self._session_file.unlink(missing_ok=True)  # не приставать при каждом запуске
        self.notify_project_state()

    def _autosave_tick(self) -> None:
        """Фоновый снимок формы: крэш не съедает настройку перед ночной очередью."""
        data = self._screen.collect_job().to_dict()
        if data != self._last_session_snapshot:
            self._last_session_snapshot = data
            with contextlib.suppress(OSError):
                RenderJob.from_dict(data).save(self._session_file)
        self.notify_project_state()

    # --- восстановление очереди после крэша/перезагрузки ------------------------

    def _offer_queue_restore(self) -> None:
        unfinished = [e for e in self._restore_entries if e.unfinished and not e.preview]
        self._restore_entries = []
        if not unfinished:
            return
        listing = "\n".join(f"• {e.title or f'Задание №{e.id}'}" for e in unfinished[:8])
        if len(unfinished) > 8:
            listing += f"\n… и ещё {len(unfinished) - 8}"
        answer = QMessageBox.question(
            self._screen,
            "LoopStudio — восстановление очереди",
            (
                f"Прошлый сеанс прервался, не завершив задания ({len(unfinished)}):\n\n"
                f"{listing}\n\n"
                "Поставить их в очередь заново? Рендер начнётся с самого начала."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._journal.clear()
            return
        restorable: list[tuple[RenderJob, str | None]] = []
        problems: list[str] = []
        for entry in unfinished:
            try:
                job = RenderJob.from_dict(entry.job)
                job.validate()
            except (JobError, TypeError) as exc:
                problems.append(f"{entry.title or f'№{entry.id}'}: {exc}")
                continue
            restorable.append((job, entry.title or None))
        if problems and not restorable:
            # все задания сейчас невалидны (сетевой диск ещё не подключён?) —
            # журнал НЕ стираем: предложим восстановление при следующем запуске
            QMessageBox.warning(
                self._screen,
                "LoopStudio",
                "Не удалось восстановить задания:\n"
                + "\n".join(problems)
                + "\n\nЕсли файлы на внешнем диске — подключите его и откройте "
                "LoopStudio заново: предложим восстановление ещё раз.",
            )
            return
        self._journal.clear()
        for job, title in restorable:
            self._enqueue(job, preview=False, title=title)
        if problems:
            QMessageBox.warning(
                self._screen,
                "LoopStudio",
                "Часть заданий не удалось восстановить:\n" + "\n".join(problems),
            )

    # --- генерация: пре-флайт и очередь ----------------------------------------

    def _collect_valid_job(self) -> RenderJob | None:
        job = self._screen.collect_job()
        try:
            job.validate()
        except JobError as exc:
            QMessageBox.warning(self._screen, "LoopStudio", str(exc))
            return None
        return job

    def _on_generate(self) -> None:
        if self._batch_folders:
            self._start_batch_generate()
            return
        job = self._collect_valid_job()
        if job is None:
            return
        if self._preflight_thread is not None and self._preflight_thread.is_alive():
            return  # проверка уже идёт
        self._screen.generate_btn.setEnabled(False)
        self._screen.generate_btn.setText("Проверяю…")
        pending_jobs = [i.job for i in self._queue.items if i.status == "pending"]

        def worker() -> None:
            try:
                report = run_preflight(job, pending=pending_jobs, probe_fn=self._cached_probe)
            except Exception as exc:  # пре-флайт — сервис: его сбой не блокирует рендер
                report = PreflightReport(
                    predicted_duration=None,
                    est_output_bytes=None,
                    est_temp_bytes=None,
                    warnings=(f"Проверка перед рендером не выполнилась: {exc}",),
                )
            self._bridge.preflight_done.emit(job, report)

        self._preflight_thread = threading.Thread(target=worker, daemon=True, name="preflight")
        self._preflight_thread.start()

    def _on_preflight_done(self, job_obj: object, report_obj: object) -> None:
        job = cast(RenderJob, job_obj)
        report = cast(PreflightReport, report_obj)
        children = self._batch_children
        self._batch_children = []
        self._screen.generate_btn.setEnabled(True)
        self._screen.generate_btn.setText("Сгенерировать")
        self._show_forecast(report)
        if report.clean or PreflightDialog.confirm(self._screen.window(), report):
            self._enqueue(job, preview=False)
            for child in children:  # остальные задания пакета
                self._enqueue(child, preview=False)
            if children:
                # пакет поставлен — сбрасываем режим, чтобы не поставить повторно
                self._on_batch_clear()

    def _show_forecast(self, report: PreflightReport) -> None:
        parts: list[str] = []
        if report.predicted_duration is not None:
            parts.append(f"итог ~{_hms(report.predicted_duration)}")
        if report.est_output_bytes:
            parts.append(f"файл ~{report.est_output_bytes / 1024**3:.1f} ГБ")
        if parts:
            self._screen.forecast_label.setText(
                "Прогноз: " + " · ".join(parts) + " (оценка приблизительная)"
            )
            self._screen.forecast_label.show()

    def _preview_job(self) -> RenderJob | None:
        """Задание для превью: в пакетном режиме — клон первой папки пакета."""
        if self._batch_folders:
            clones = self._batch_clones()
            return clones[0] if clones else None
        return self._collect_valid_job()

    def _preview_already_queued(self) -> bool:
        """Защита от серии кликов: пять нажатий — не пять превью в очереди."""
        if any(i.preview and i.status in ("pending", "running") for i in self._queue.items):
            self._screen.progress_label.setText("Превью уже готовится — дождитесь его.")
            return True
        return False

    def _on_preview(self) -> None:
        if self._preview_already_queued():
            return
        job = self._preview_job()
        if job is None:
            return
        # предпросмотр — быстрый путь без пре-флайта: важна скорость итераций
        self._enqueue(job, preview=True)

    def _on_preview_start(self) -> None:
        """«Начало ролика»: детерминированное превью с 00:00 — без лотереи."""
        if self._preview_already_queued():
            return
        job = self._preview_job()
        if job is None:
            return
        self._enqueue(job, preview=True, preview_from_start=True)

    def _on_seams(self) -> None:
        """«Проверить швы»: нарезка стыков лупа/интро/аутро в фоне."""
        if self._seams_thread is not None and self._seams_thread.is_alive():
            self._seams_cancel.set()  # повторный клик — отмена
            return
        if self._queue.busy:
            QMessageBox.information(
                self._screen,
                "LoopStudio",
                "Идёт рендер очереди — нарезку швов лучше делать после него, "
                "чтобы не замедлять основной рендер.",
            )
            return
        # для нарезки нужна только видео-часть формы — музыку не требуем
        job = self._screen.collect_job()
        if not job.video_path or not Path(job.video_path).is_file():
            QMessageBox.warning(self._screen, "LoopStudio", "Не выбрано основное видео (шаг 1).")
            return
        self._seams_cancel = threading.Event()
        cancel = self._seams_cancel
        self._screen.seams_btn.setText("Готовлю нарезку… (клик — отмена)")
        out = Path(tempfile.gettempdir()) / "loopstudio_швы.mp4"

        def worker() -> None:
            try:
                build_seams_preview(job, out, cancel=cancel)
            except FFmpegCancelled:
                self._bridge.seams_failed.emit("")
                return
            except (FFmpegError, ProbeError, OSError, RuntimeError) as exc:
                self._bridge.seams_failed.emit(str(exc))
                return
            self._bridge.seams_ready.emit(str(out))

        self._seams_thread = threading.Thread(target=worker, daemon=True, name="seams")
        self._seams_thread.start()

    def _on_seams_ready(self, path: str) -> None:
        self._screen.seams_btn.setText("Проверить швы")
        os.startfile(path)

    def _on_seams_failed(self, message: str) -> None:
        self._screen.seams_btn.setText("Проверить швы")
        if message:
            QMessageBox.warning(
                self._screen, "LoopStudio", f"Не удалось собрать нарезку швов:\n{message}"
            )

    # --- перемешивание и линтер громкости (шаг 2) --------------------------------

    def _music_track_paths(self) -> list[Path]:
        """Пути треков в порядке списка шага 2 (включая исключённые)."""
        folder = self._screen.music_picker.path()
        if not folder:
            return []
        base = Path(folder)
        return [base / name for name in self._screen.track_order()]

    def _on_shuffle(self) -> None:
        tracks = self._music_track_paths()
        if len(tracks) < 2:
            return
        if self._shuffle_thread is not None and self._shuffle_thread.is_alive():
            return
        keep_first = self._screen.shuffle_keep_first.isChecked()
        self._screen.shuffle_btn.setEnabled(False)
        self._screen.shuffle_btn.setText("Перемешиваю…")

        def worker() -> None:
            pairs: list[tuple[str, str | None]] = []
            for track in tracks:
                artist: str | None = None
                if track.is_file():
                    with contextlib.suppress(ProbeError, OSError):
                        artist = self._cached_probe(track).artist
                pairs.append((track.name, artist))
            order = smart_shuffle(pairs, keep_first=keep_first)
            self._bridge.shuffle_done.emit(order)

        self._shuffle_thread = threading.Thread(target=worker, daemon=True, name="shuffle")
        self._shuffle_thread.start()

    def _on_shuffle_done(self, order_obj: object) -> None:
        order = cast(list[str], order_obj)
        excluded = self._screen.excluded_tracks()
        self._screen.set_track_states(order, excluded)
        self._apply_lint_marks()  # вернуть бейджи линтера после пересборки списка
        self._screen.shuffle_btn.setEnabled(True)
        self._screen.shuffle_btn.setText("Перемешать")
        self.notify_project_state()

    def _on_lint(self) -> None:
        tracks = [t for t in self._music_track_paths() if t.is_file()]
        if not tracks:
            QMessageBox.information(
                self._screen, "LoopStudio", "Сначала выберите папку с музыкой (шаг 2)."
            )
            return
        if self._lint_thread is not None and self._lint_thread.is_alive():
            return
        self._screen.lint_btn.setEnabled(False)

        def worker() -> None:
            results: dict[str, TrackAnalysis] = {}
            failed: list[str] = []
            for i, track in enumerate(tracks, start=1):
                self._bridge.lint_progress.emit(f"Анализ {i}/{len(tracks)}…")
                try:
                    info = self._cached_probe(track)
                    results[track.name] = analyze_track(track, info.duration)
                except (AnalyzeError, ProbeError, OSError):
                    failed.append(track.name)  # молча пропускать нельзя — часто
                    continue  # именно нечитаемый файл и есть проблема плейлиста
            self._bridge.lint_done.emit((results, failed))

        self._lint_thread = threading.Thread(target=worker, daemon=True, name="lint")
        self._lint_thread.start()

    def _on_lint_progress(self, text: str) -> None:
        self._screen.lint_btn.setText(text)

    def _on_lint_done(self, results_obj: object) -> None:
        results, failed = cast("tuple[dict[str, TrackAnalysis], list[str]]", results_obj)
        self._lint_results = results
        self._screen.lint_btn.setEnabled(True)
        self._screen.lint_btn.setText("Анализ громкости")
        if failed:
            listing = "\n".join(f"• {name}" for name in failed[:10])
            if len(failed) > 10:
                listing += f"\n… и ещё {len(failed) - 10}"
            QMessageBox.warning(
                self._screen,
                "LoopStudio",
                f"Не удалось проанализировать треки (файл повреждён?):\n{listing}",
            )
        self._apply_lint_marks()

    def _apply_lint_marks(self) -> None:
        """Бейджи линтера: цвет и тултип по замерам, сводка под списком."""
        results = self._lint_results
        if not results:
            return
        levels = sorted(a.loudness.input_i for a in results.values() if a.loudness is not None)
        if not levels:
            return
        median = levels[len(levels) // 2]
        loud_count = 0
        lint_lines: list[str] = []
        lst = self._screen.track_list
        for i in range(lst.count()):
            item = lst.item(i)
            if item is None:
                continue
            analysis = results.get(item.text())
            if analysis is None or analysis.loudness is None:
                continue
            delta = analysis.loudness.input_i - median
            notes: list[str] = [f"Громкость: {analysis.loudness.input_i:.1f} LUFS"]
            color: str | None = None
            if analysis.loudness.input_tp > -0.2:
                color = "#e05c5c"
                notes.append("true peak ~0 дБ — возможен клиппинг")
            elif delta >= 4.0:
                color = "#d9a441"
                loud_count += 1
                notes.append(f"громче медианы плейлиста на {delta:.1f} дБ")
            elif delta <= -4.0:
                color = "#7f8fa6"
                notes.append(f"тише медианы плейлиста на {abs(delta):.1f} дБ")
            if analysis.tail_silence > 1.5:
                notes.append(
                    f"тишина в хвосте ~{analysis.tail_silence:.0f} с — включите «Обрезать тишину»"
                )
            if color is not None:
                item.setForeground(QColor(color))
            item.setToolTip("\n".join(notes))
        spread = levels[-1] - levels[0]
        summary = (
            f"Громкость треков: разброс {spread:.1f} дБ ({levels[0]:.1f}…{levels[-1]:.1f} LUFS)."
        )
        if spread > 4.0 and not self._screen.normalize_toggle.isChecked():
            summary += (
                f" Выбивающихся: {loud_count}. Совет: включите нормализацию "
                f"(−14 LUFS) ниже — треки выровняются при рендере."
            )
        elif spread <= 4.0:
            summary += " Плейлист ровный ✓"
        lint_lines.append(summary)
        self._screen.lint_summary.setText("\n".join(lint_lines))
        self._screen.lint_summary.show()

    def _on_stop_playback(self) -> None:
        self._player.stop()
        self._playing_path = ""

    # --- встроенная библиотека ассетов ------------------------------------------

    def _on_ambient_builtin(self, row_obj: object) -> None:
        row = cast(AmbientRow, row_obj)
        assets = list_ambients()
        if not assets:
            QMessageBox.information(
                self._screen,
                "LoopStudio",
                "Встроенная библиотека звуков не установлена в этой сборке.",
            )
            return
        dialog = AssetGalleryDialog(self._screen.window(), "Встроенные звуки эмбиента", assets)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_path():
            row.set_source(dialog.selected_path())
            self.notify_project_state()

    def _on_overlay_builtin(self) -> None:
        assets = list_overlays()
        if not assets:
            QMessageBox.information(
                self._screen,
                "LoopStudio",
                "Встроенная библиотека VFX-оверлеев не установлена в этой сборке.",
            )
            return
        dialog = AssetGalleryDialog(self._screen.window(), "Встроенные VFX-оверлеи", assets)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_path():
            self._screen.overlay_picker.setPath(dialog.selected_path())
            self.notify_project_state()

    # --- быстрая настройка видео ------------------------------------------------

    def run_quick_setup(self) -> None:
        """«Быстрая настройка видео»: 4 шага — и сразу полноценная генерация.

        Диалог предзаполняется текущей формой (что видишь — то и получишь),
        поля переносятся в штатную форму как есть, рендер стартует тем же
        путём, что и кнопка «Сгенерировать» шага 10. Открывается только
        по кнопке вверху формы или из меню — никаких автопоказов.
        """
        dialog = QuickSetupDialog(
            self._screen.window(),
            default_output=self._settings.last_output_dir,
            initial_video=self._screen.video_picker.path(),
            initial_intro=self._screen.intro_picker.path(),
            initial_outro=self._screen.outro_picker.path(),
            initial_music=self._screen.music_picker.path(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # заполняем ШТАТНУЮ форму — дальше всё идёт обычным путём
        self._screen.video_picker.setPath(dialog.video_picker.path())
        self._screen.intro_picker.setPath(dialog.intro_picker.path())
        self._screen.outro_picker.setPath(dialog.outro_picker.path())
        self._screen.music_picker.setPath(dialog.music_picker.path())
        self._screen.output_picker.setPath(dialog.output_picker.path())
        self._screen.duration_slider.setValue(dialog.duration_minutes)
        first = dialog.first_track_picker.path()
        if first:
            name = Path(first).name
            order = self._screen.track_order()
            if name in order:
                self._screen.set_track_states(
                    [name] + [n for n in order if n != name], self._screen.excluded_tracks()
                )
        # прогресс — там же, где у обычной генерации: переходим на шаг «Рендер»
        self._screen.go_to_step(len(self._screen.pages) - 1)
        self.notify_project_state()
        self._on_generate()

    def _on_batch(self) -> None:
        """Пакетный режим: только ВЫБОР папок. Рендер стартует «Сгенерировать»."""
        start_dir = self._screen.music_picker.path() or self._settings.last_music_dir
        dialog = BatchDialog(self._screen.window(), start_dir)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folders = dialog.selected_folders()
        if not folders:
            return
        self._batch_folders = folders
        self._screen.set_batch_state(len(folders))
        self._settings.last_music_dir = str(folders[0].parent)
        self._settings.save()

    def _on_batch_clear(self) -> None:
        self._batch_folders = []
        self._screen.set_batch_state(None)

    def _batch_clones(self, *, quiet: bool = False) -> list[RenderJob]:
        """Клоны задания по выбранным папкам пакета (пустой список = ошибка)."""
        base = self._screen.collect_job()
        try:
            base.validate(require_music=False)  # музыку подставляют клоны
            clones = [clone_job_for_folder(base, folder) for folder in self._batch_folders]
            for clone in clones:
                clone.validate()
        except JobError as exc:
            if not quiet:
                QMessageBox.warning(self._screen, "LoopStudio", str(exc))
            return []
        return clones

    def _start_batch_generate(self) -> None:
        """«Сгенерировать» в пакетном режиме: пре-флайт первого, постановка всех."""
        clones = self._batch_clones()
        if not clones:
            return
        if self._preflight_thread is not None and self._preflight_thread.is_alive():
            return
        first, rest = clones[0], clones[1:]
        self._batch_children = rest
        self._screen.generate_btn.setEnabled(False)
        self._screen.generate_btn.setText("Проверяю…")
        queued = [i.job for i in self._queue.items if i.status == "pending"]
        pending_jobs = queued + rest

        def worker() -> None:
            try:
                report = run_preflight(first, pending=pending_jobs, probe_fn=self._cached_probe)
            except Exception as exc:  # пре-флайт — сервис: его сбой не блокирует рендер
                report = PreflightReport(
                    predicted_duration=None,
                    est_output_bytes=None,
                    est_temp_bytes=None,
                    warnings=(f"Проверка перед рендером не выполнилась: {exc}",),
                )
            self._bridge.preflight_done.emit(first, report)

        self._preflight_thread = threading.Thread(target=worker, daemon=True, name="preflight")
        self._preflight_thread.start()

    def _enqueue(
        self,
        job: RenderJob,
        *,
        preview: bool,
        preview_from_start: bool = False,
        title: str | None = None,
    ) -> None:
        if self._shutdown_dialog is not None:
            self._shutdown_dialog.reject()  # новое задание отменяет выключение
        label = title if title is not None else self._display_title(job)
        self._queue.add(job, preview=preview, preview_from_start=preview_from_start, title=label)

    def _display_title(self, job: RenderJob) -> str:
        """Человекочитаемое имя задания для строк очереди и уведомлений."""
        folder = Path(job.music_dir).name if job.music_dir else ""
        text = job.title_template.replace("{date}", date.today().isoformat())
        text = text.replace("{duration}", _human_duration(job.duration_sec))
        text = text.replace("{folder}", folder)
        text = text.strip() or "LoopStudio"
        if folder and folder not in text:
            text += f" · музыка: {folder}"
        return text

    def _on_cancel(self) -> None:
        for item in self._queue.items:
            if item.status == "running":
                self._queue.cancel_item(item.id)
                return

    def _on_open_folder(self) -> None:
        folder = self._screen.output_picker.path() or self._settings.last_output_dir
        if folder and Path(folder).is_dir():
            os.startfile(folder)
        else:
            QMessageBox.information(
                self._screen, "LoopStudio", "Папка сохранения ещё не выбрана (шаг 7)."
            )

    def _remember_output_dir(self, path: str) -> None:
        if path:
            self._settings.last_output_dir = path
            self._settings.save()

    # --- строки очереди ---------------------------------------------------------

    def _row_of(self, item_id: int) -> int | None:
        lw = self._screen.queue_list
        for row in range(lw.count()):
            row_item = lw.item(row)
            if row_item is not None and row_item.data(Qt.ItemDataRole.UserRole) == item_id:
                return row
        return None

    def _find_item(self, item_id: int) -> QueueItem | None:
        for item in self._queue.items:
            if item.id == item_id:
                return item
        return None

    def _fill_row(self, list_item: QListWidgetItem, item: QueueItem) -> None:
        """Данные строки очереди для делегата (номер, статус, имя, мета, бейдж)."""
        kind = "Предпросмотр" if item.preview else "Ролик"
        meta_parts = [kind.lower()]
        took = item.duration_seconds
        if item.status in TERMINAL_STATUSES and took is not None:
            meta_parts.append(f"за {_mmss(took)}")
        badge = _STATUS_LABELS[item.status].upper().rstrip("…")
        list_item.setData(QUEUE_ROLE_STATUS, item.status)
        list_item.setData(QUEUE_ROLE_TITLE, item.title or f"{kind} №{item.id}")
        list_item.setData(QUEUE_ROLE_META, " · ".join(meta_parts))
        if item.status != "running" or not list_item.data(QUEUE_ROLE_BADGE):
            list_item.setData(QUEUE_ROLE_BADGE, badge)
        list_item.setToolTip(self._row_tooltip(item))

    def _row_text(self, item: QueueItem) -> str:
        kind = "Предпросмотр" if item.preview else "Ролик"
        parts = [f"№{item.id} · {kind}"]
        if item.title:
            parts.append(item.title)
        parts.append(_STATUS_LABELS[item.status])
        took = item.duration_seconds
        if item.status in TERMINAL_STATUSES and took is not None:
            parts.append(f"за {_mmss(took)}")
        text = " · ".join(parts)
        if item.status == "failed" and item.error:
            text += f" — {item.error.splitlines()[0]}"
        if item.result is not None and item.result.warnings:
            text += " · ⚠"
        return text

    def _row_tooltip(self, item: QueueItem) -> str:
        lines: list[str] = []
        if item.error:
            lines.append(item.error)
        if item.result is not None:
            lines.append(f"Файл: {item.result.output_path}")
            lines.extend(f"⚠ {w}" for w in item.result.warnings)
        lines.append("Правый клик — действия: повторить, открыть, убрать из списка.")
        return "\n".join(lines)

    def _refresh_row(self, item: QueueItem) -> None:
        lw = self._screen.queue_list
        row = self._row_of(item.id)
        if row is None:
            # DisplayRole хранит текст для скринридеров; рисует всё делегат
            list_item = QListWidgetItem(self._row_text(item))
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            self._fill_row(list_item, item)
            lw.addItem(list_item)
            lw.scrollToBottom()
            return
        list_item_existing = lw.item(row)
        if list_item_existing is not None:
            list_item_existing.setText(self._row_text(item))
            self._fill_row(list_item_existing, item)

    # --- события очереди --------------------------------------------------------

    def _on_item_updated(self, item_obj: object) -> None:
        item = cast(QueueItem, item_obj)
        self._refresh_row(item)
        running = any(i.status == "running" for i in self._queue.items)
        self._screen.cancel_btn.setEnabled(running)
        if item.status == "done":
            self._screen.progress_bar.setValue(1000)
            took = item.duration_seconds
            text = f"Готово за {_mmss(took)}" if took is not None else "Готово"
            if item.result is not None and item.result.warnings:
                text += " · ⚠ " + "; ".join(item.result.warnings)
            self._screen.progress_label.setText(text)
            if item.preview and item.result is not None:
                os.startfile(str(item.result.output_path))
            elif not item.preview:
                note = f" · за {_mmss(took)}" if took is not None else ""
                self._notify_user("Ролик готов", f"{item.title or f'№{item.id}'}{note}")
        elif item.status == "failed":
            self._screen.progress_label.setText(f"Ошибка: {item.error}")
            self._last_error_log = self._extract_log_path(item.error or "")
            self._screen.open_log_btn.setVisible(self._last_error_log is not None)
            first_line = (item.error or "").splitlines()[0] if item.error else ""
            self._notify_user(
                "Ошибка рендера", f"{item.title or f'№{item.id}'}: {first_line[:140]}"
            )
        elif item.status == "cancelled":
            if not self._queue.busy:
                self._screen.progress_label.setText("Отменено")
                self._screen.progress_bar.setValue(0)
        self._update_power_state()

    def _on_item_progress(self, item_obj: object, update_obj: object) -> None:
        item = cast(QueueItem, item_obj)
        update = cast(ProgressUpdate, update_obj)
        self._screen.progress_bar.setValue(round(update.fraction * 1000))
        timing = f" · прошло {_mmss(update.elapsed_seconds)}"
        if update.eta_seconds is not None:
            timing += f" · осталось ~{_mmss(update.eta_seconds)}"
        text = (
            f"REC · №{item.id} · ЭТАП {update.stage_index}/{update.stage_count} · "
            f"{update.stage_name.upper()}{timing}"
        )
        self._last_progress_text = text
        self._last_progress_at = time.monotonic()
        self._screen.progress_label.setText(text)
        # бейдж строки очереди показывает живой процент рендера
        row = self._row_of(item.id)
        if row is not None:
            list_item = self._screen.queue_list.item(row)
            if list_item is not None:
                list_item.setData(QUEUE_ROLE_BADGE, f"РЕНДЕР {round(update.fraction * 100)}%")

    def _on_pulse(self) -> None:
        """ffmpeg молчит (финализация большого файла) — процесс жив, говорим об этом."""
        if self._last_progress_at is None or not self._last_progress_text:
            return
        if not any(i.status == "running" for i in self._queue.items):
            return
        if time.monotonic() - self._last_progress_at > 6.0:
            self._screen.progress_label.setText(
                f"{self._last_progress_text}\nФайл дописывается на диск (финализация) — "
                f"для длинного 4K-ролика это может занять несколько минут, процесс работает."
            )

    # --- питание и завершение очереди -------------------------------------------

    def _terminal_counts(self) -> tuple[int, int]:
        items = [i for i in self._queue.items if not i.preview]
        done = sum(1 for i in items if i.status == "done")
        failed = sum(1 for i in items if i.status == "failed")
        return done, failed

    def _update_power_state(self) -> None:
        busy = self._queue.busy
        if busy == self._was_busy:
            return
        self._was_busy = busy
        if busy:
            self._counts_at_start = self._terminal_counts()  # сводка — только за партию
            power.keep_system_awake(True)
            self._screen.keep_awake_label.setText(
                "Сон Windows отключён до конца очереди: экран может гаснуть, рендер "
                "продолжится. Закрытую крышку ноутбука это не перекрывает."
            )
            self._screen.keep_awake_label.show()
        else:
            power.keep_system_awake(False)
            self._screen.keep_awake_label.hide()
            self._on_queue_finished()

    def _on_queue_finished(self) -> None:
        done_total, failed_total = self._terminal_counts()
        done = done_total - self._counts_at_start[0]
        failed = failed_total - self._counts_at_start[1]
        summary = f"Готово: {done} · с ошибкой: {failed}"
        if done + failed >= 2:
            self._notify_user("Очередь завершена", summary)
        if (done + failed) > 0 and self._screen.shutdown_toggle.isChecked():
            self._start_shutdown_countdown(summary)

    def _start_shutdown_countdown(self, summary: str) -> None:
        if self._shutdown_dialog is not None:
            return
        dialog = ShutdownCountdownDialog(
            self._screen.window(), _SHUTDOWN_COUNTDOWN_SEC, summary=f"Очередь завершена. {summary}."
        )
        self._shutdown_dialog = dialog
        try:
            result = dialog.exec()
        finally:
            self._shutdown_dialog = None
        if result == QDialog.DialogCode.Accepted:
            if power.schedule_shutdown(_SHUTDOWN_GRACE_SEC):
                self._screen.progress_label.setText(
                    f"Компьютер выключится через {_SHUTDOWN_GRACE_SEC} секунд…"
                )
                # последний шанс передумать: раньше запланированное выключение
                # было не отменить ничем — прямая потеря работы других программ
                box = QMessageBox(self._screen.window())
                box.setWindowTitle("LoopStudio — выключение")
                box.setIcon(QMessageBox.Icon.Information)
                box.setText(
                    f"Компьютер выключится через {_SHUTDOWN_GRACE_SEC} секунд.\n"
                    "Передумали — нажмите «Отменить выключение»."
                )
                box.addButton("Отменить выключение", QMessageBox.ButtonRole.RejectRole)
                box.exec()
                if power.cancel_shutdown():
                    self._screen.progress_label.setText("Выключение отменено.")
            else:
                QMessageBox.warning(
                    self._screen,
                    "LoopStudio",
                    "Не удалось запланировать выключение — выключите компьютер вручную.",
                )

    # --- итоги и контекстное меню очереди ----------------------------------------

    def _show_summary(self) -> None:
        items = self._queue.items
        if not items:
            QMessageBox.information(
                self._screen, "LoopStudio", "Очередь пуста — заданий ещё не было."
            )
            return
        QueueSummaryDialog(self._screen.window(), items).exec()

    def _queue_menu(self, pos: QPoint) -> None:
        lw = self._screen.queue_list
        list_item = lw.itemAt(pos)
        if list_item is None:
            return
        item = self._find_item(int(list_item.data(Qt.ItemDataRole.UserRole)))
        if item is None:
            return
        menu = QMenu(lw)
        if item.status == "done" and item.result is not None:
            menu.addAction("Открыть ролик", lambda: self._open_result(item))
            menu.addAction("Показать в папке", lambda: self._reveal_result(item))
            if item.result.passport_path is not None:
                menu.addAction("Открыть паспорт рендера", lambda: self._open_passport(item))
        if item.status in TERMINAL_STATUSES and not item.preview:
            menu.addAction("Повторить (поставить заново)", lambda: self._retry_item(item))
        menu.addAction("Загрузить настройки в форму", lambda: self._load_item_to_form(item))
        if item.status in ("pending", "running"):
            menu.addAction("Отменить", lambda: self._queue.cancel_item(item.id))
        if item.status in TERMINAL_STATUSES:
            menu.addAction("Убрать из списка", lambda: self._remove_item_row(item))
        menu.exec(lw.mapToGlobal(pos))

    def _open_result(self, item: QueueItem) -> None:
        if item.result is not None and item.result.output_path.is_file():
            os.startfile(str(item.result.output_path))
        else:
            QMessageBox.warning(
                self._screen, "LoopStudio", "Файл не найден — его могли переместить."
            )

    def _reveal_result(self, item: QueueItem) -> None:
        if item.result is None:
            return
        path = item.result.output_path
        if path.is_file():
            # stdin=DEVNULL обязателен для subprocess в оконном exe;
            # абсолютный путь — чтобы не подхватить чужой explorer.exe рядом
            explorer = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "explorer.exe"
            subprocess.Popen(
                [str(explorer), f"/select,{path}"],
                stdin=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
        elif path.parent.is_dir():
            os.startfile(str(path.parent))

    def _open_passport(self, item: QueueItem) -> None:
        if (
            item.result is not None
            and item.result.passport_path is not None
            and item.result.passport_path.is_file()
        ):
            os.startfile(str(item.result.passport_path))

    def _retry_item(self, item: QueueItem) -> None:
        job = RenderJob.from_dict(item.job.to_dict())  # глубокая копия
        try:
            job.validate()
        except JobError as exc:
            QMessageBox.warning(self._screen, "LoopStudio", f"Нельзя повторить: {exc}")
            return
        self._enqueue(job, preview=False, title=item.title or None)

    def _load_item_to_form(self, item: QueueItem) -> None:
        self._screen.apply_job(RenderJob.from_dict(item.job.to_dict()))

    def _remove_item_row(self, item: QueueItem) -> None:
        if self._queue.remove_item(item.id):
            row = self._row_of(item.id)
            if row is not None:
                self._screen.queue_list.takeItem(row)

    # --- инфо о выбранном видео ---------------------------------------------------

    def _cached_probe(self, path: Path) -> MediaInfo:
        """probe с кэшем по (путь, mtime, размер) — для пре-флайта и инфо-строки."""
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime, stat.st_size)
        except OSError:
            return probe(path)  # даст понятную ProbeError
        with self._probe_cache_lock:  # кэш читают несколько фоновых потоков
            cached = self._probe_cache.get(key)
        if cached is None:
            cached = probe(path)
            with self._probe_cache_lock:
                self._probe_cache[key] = cached
        return cached

    def _on_video_path_changed(self, path: str) -> None:
        label = self._screen.video_info
        if not path:
            label.hide()
            label.setText("")
            return
        label.setText("Анализирую видео…")
        label.show()

        def worker(p: str = path) -> None:
            try:
                info = self._cached_probe(Path(p))
            except ProbeError as exc:
                self._bridge.video_probed.emit(p, exc)
                return
            self._bridge.video_probed.emit(p, info)

        threading.Thread(target=worker, daemon=True, name="video-probe").start()

    def _on_video_probed(self, path: str, payload: object) -> None:
        if path != self._screen.video_picker.path():
            return  # пользователь уже выбрал другой файл
        label = self._screen.video_info
        if isinstance(payload, ProbeError):
            label.setText(f"⚠ {payload}")
            label.show()
            return
        info = cast(MediaInfo, payload)
        parts: list[str] = []
        if info.width and info.height:
            parts.append(f"{info.width}×{info.height}")
        if info.fps:
            parts.append(f"{info.fps:.0f} кадр/с")
        parts.append(_mmss(info.duration))
        parts.append("есть звук" if info.has_audio else "без звука")
        text = " · ".join(parts)
        if info.width and info.height and info.height > info.width:
            text += "\n⚠ Видео вертикальное — в горизонтальном ролике будут чёрные полосы по бокам."
        label.setText(text)
        label.show()

    @staticmethod
    def _extract_log_path(error_text: str) -> Path | None:
        """Достать путь лог-файла из текста ошибки пайплайна."""
        match = re.search(r"в логе:\s*\n?(.+?\.txt)", error_text)
        if match:
            candidate = Path(match.group(1).strip())
            if candidate.is_file():
                return candidate
        return None

    def _open_error_log(self) -> None:
        if self._last_error_log is not None and self._last_error_log.is_file():
            os.startfile(str(self._last_error_log))

    # --- прослушивание ----------------------------------------------------------

    def _on_play_file(self, path: str, volume: float = 1.0) -> None:
        if not path:
            return
        if self._playing_path == path and self._player.isPlaying():
            self._player.stop()
            self._playing_path = ""
            return
        self._playing_path = path
        self._player.stop()
        # ▶ слоя играет с громкостью его слайдера — баланс слышен сразу
        self._audio_out.setVolume(max(0.0, min(1.0, volume)))
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def _on_listen_mix(self) -> None:
        """Быстрый предрендер 30 сек (музыка + включённые шары) и воспроизведение."""
        if self._mix_thread is not None and self._mix_thread.is_alive():
            return
        job = self._screen.collect_job()
        if not job.music_dir or not Path(job.music_dir).is_dir():
            QMessageBox.warning(self._screen, "LoopStudio", "Сначала выберите папку с музыкой.")
            return
        self._screen.listen_mix_btn.setEnabled(False)
        self._screen.listen_mix_btn.setText("Собираю микс…")
        # остановить текущее воспроизведение и убрать прошлый файл превью:
        # уникальные имена исключают запись в файл, открытый плеером
        self._player.stop()
        self._playing_path = ""
        if self._mix_preview_file is not None:
            with contextlib.suppress(OSError):
                self._mix_preview_file.unlink(missing_ok=True)

        def worker() -> None:
            try:
                # тот же порядок и исключения, что и в рендере (фикс: раньше
                # превью игнорировало ручной порядок и играло по сортировке папки)
                tracks = _ordered_tracks(job)
                playlist = [(t, probe(t).duration) for t in tracks]
                timeline = build_timeline(
                    playlist, _MIX_PREVIEW_SECONDS, pause=job.pause, crossfade=job.crossfade
                )
                ambient = [
                    AmbientLayer(path=Path(a.path), volume=a.volume)
                    for a in job.ambient
                    if a.enabled and a.path
                ]
                # внутри — FLAC, расширение должно совпадать с содержимым
                out = Path(tempfile.gettempdir()) / f"loopstudio_mix_{uuid.uuid4().hex[:8]}.flac"
                with tempfile.TemporaryDirectory(prefix="loopstudio_mix_") as work:
                    build_master(
                        timeline,
                        MixdownSettings(
                            fade_out=job.fade_out,
                            normalize=job.normalize,
                            music_volume=job.music_volume,
                        ),
                        ambient,
                        out,
                        work_dir=Path(work),
                    )
                self._bridge.mix_ready.emit(str(out))
            except (
                TimelineError,
                ProbeError,
                JobError,
                NotADirectoryError,
                RuntimeError,
                OSError,
            ) as exc:
                self._bridge.mix_failed.emit(str(exc))

        self._mix_thread = threading.Thread(target=worker, daemon=True, name="mix-preview")
        self._mix_thread.start()

    def _on_mix_ready(self, path: str) -> None:
        self._screen.listen_mix_btn.setEnabled(True)
        self._screen.listen_mix_btn.setText("Прослушать микс")
        self._mix_preview_file = Path(path)
        self._on_play_file(path)

    def _on_mix_failed(self, message: str) -> None:
        self._screen.listen_mix_btn.setEnabled(True)
        self._screen.listen_mix_btn.setText("Прослушать микс")
        QMessageBox.warning(self._screen, "LoopStudio", f"Не удалось собрать микс:\n{message}")

    # --- пресеты микса ------------------------------------------------------------

    def _refresh_presets(self) -> None:
        combo = self._screen.mix_preset_combo
        combo.clear()
        # заводские пресеты (★) сверху, затем пользовательские
        combo.addItems(sorted(factory_presets()))
        combo.addItems(sorted(self._settings.mix_presets))

    def _preset_data(self, name: str) -> dict[str, Any] | None:
        """Данные пресета: заводской или пользовательский."""
        if name.startswith(FACTORY_PREFIX):
            return factory_presets().get(name)
        return self._settings.mix_presets.get(name)

    def _on_save_preset(self) -> None:
        name = NameDialog.get_text(
            self._screen.window(),
            title="Пресет микса",
            hint="Название пресета — например «Мой дождливый вечер»",
        )
        if not name:
            return
        if name.strip().startswith(FACTORY_PREFIX.strip()):
            QMessageBox.information(
                self._screen,
                "LoopStudio",
                "Имена со звёздочкой ★ зарезервированы за встроенными пресетами — "
                "выберите другое название.",
            )
            return
        job = self._screen.collect_job()
        self._settings.mix_presets[name.strip()] = {
            "music_volume": job.music_volume,
            "ambient": [
                {"enabled": a.enabled, "path": a.path, "volume": a.volume} for a in job.ambient
            ],
        }
        self._settings.save()
        self._refresh_presets()
        self._screen.mix_preset_combo.setCurrentText(name.strip())

    def _on_delete_preset(self) -> None:
        name = self._screen.mix_preset_combo.currentText()
        if name.startswith(FACTORY_PREFIX):
            QMessageBox.information(
                self._screen,
                "LoopStudio",
                "Это встроенный пресет — удалить его нельзя. Сохраните свой "
                "вариант под новым именем.",
            )
            return
        if not name or name not in self._settings.mix_presets:
            return
        answer = QMessageBox.question(
            self._screen,
            "LoopStudio",
            f"Удалить пресет микса «{name}»? Отменить это будет нельзя.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            del self._settings.mix_presets[name]
            self._settings.save()
            self._refresh_presets()

    def _on_apply_preset(self, name: str) -> None:
        preset = self._preset_data(name)
        if not preset:
            return
        self._screen.music_volume.setFraction(float(preset.get("music_volume", 1.0)))
        layers: list[dict[str, Any]] = list(preset.get("ambient", []))
        missing: list[str] = []
        for row, layer in zip(self._screen.ambient_rows, layers, strict=False):
            path = str(layer.get("path", ""))
            enabled = bool(layer.get("enabled", False))
            if enabled and path and not Path(path).is_file():
                # файл слоя переехал/удалён: слой выключаем, а не роняем рендер ночью
                missing.append(Path(path).name)
                enabled = False
            row.toggle.setChecked(enabled)
            row.picker.setPath(path)
            row.volume.setFraction(float(layer.get("volume", 0.5)))
        if missing:
            QMessageBox.warning(
                self._screen,
                "LoopStudio",
                "В пресете не найдены звуки (файлы переехали или удалены):\n• "
                + "\n• ".join(missing)
                + "\n\nЭти слои выключены — укажите файлы заново (шаг 3).",
            )

    # --- профили каналов ----------------------------------------------------------

    def _refresh_profiles(self) -> None:
        combo = self._screen.profile_combo
        combo.clear()
        combo.addItems(sorted(self._settings.channel_profiles))

    def _on_save_profile(self) -> None:
        name = NameDialog.get_text(
            self._screen.window(),
            title="Профиль канала",
            hint="Название профиля — обычно имя канала, например «Rain Sleep»",
            initial=self._screen.profile_combo.currentText(),
        )
        if not name:
            return
        data = self._screen.collect_job().to_dict()
        for field in _PROFILE_EXCLUDED_FIELDS:
            data.pop(field, None)
        self._settings.channel_profiles[name.strip()] = data
        self._settings.save()
        self._refresh_profiles()
        self._screen.profile_combo.setCurrentText(name.strip())

    def _on_delete_profile(self) -> None:
        name = self._screen.profile_combo.currentText()
        if not name or name not in self._settings.channel_profiles:
            return
        answer = QMessageBox.question(
            self._screen,
            "LoopStudio",
            f"Удалить профиль канала «{name}»? Отменить это будет нельзя.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            del self._settings.channel_profiles[name]
            self._settings.save()
            self._refresh_profiles()

    def _on_apply_profile(self, name: str) -> None:
        profile = self._settings.channel_profiles.get(name)
        if not profile:
            return
        # профиль ложится поверх текущей формы: папка музыки, порядок треков
        # и длительность остаются как есть — они меняются от ролика к ролику
        current = self._screen.collect_job().to_dict()
        current.update(profile)
        self._screen.apply_job(RenderJob.from_dict(current))
        self.notify_project_state()

    # --- проекты .lsproj -----------------------------------------------------------

    def notify_project_state(self) -> None:
        """Сообщить окну суффикс заголовка: « — Имя проекта *» или пусто."""
        if self._current_project is None:
            self.project_state_changed.emit("")
            return
        dirty = self._screen.collect_job().to_dict() != self._saved_snapshot
        star = " *" if dirty else ""
        self.project_state_changed.emit(f" — {self._current_project.stem}{star}")

    @property
    def current_project(self) -> Path | None:
        return self._current_project

    def save_current(self) -> bool:
        """Тихое Ctrl+S в открытый проект. False = проекта нет, нужен «Сохранить как»."""
        if self._current_project is None:
            return False
        self.save_project(self._current_project)
        return True

    def save_project(self, path: Path) -> None:
        job = self._screen.collect_job()
        try:
            job.save(path)
        except OSError as exc:
            QMessageBox.warning(self._screen, "LoopStudio", f"Не удалось сохранить проект:\n{exc}")
            return
        self._current_project = path
        self._saved_snapshot = job.to_dict()
        self._settings.last_project_dir = str(path.parent)
        self._settings.remember_project(str(path))
        self._settings.save()
        self.notify_project_state()

    def open_project(self, path: Path) -> None:
        try:
            job = RenderJob.load(path)
        except JobError as exc:
            QMessageBox.warning(self._screen, "LoopStudio", str(exc))
            return
        self._warn_if_newer_schema(path)
        check = check_project(job)
        if not check.clean:
            dialog = ProjectIssuesDialog(self._screen.window(), check)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if dialog.relink_requested:
                job = self._relink_flow(job, path)
        self._screen.apply_job(job)
        if check.playlist.added:
            self._screen.mark_new_tracks(set(check.playlist.added))
        self._current_project = path
        self._saved_snapshot = self._screen.collect_job().to_dict()
        self._settings.last_project_dir = str(path.parent)
        self._settings.remember_project(str(path))
        self._settings.save()
        self.notify_project_state()

    def _relink_flow(self, job: RenderJob, project_path: Path) -> RenderJob:
        """«Указать новый корень»: починить переехавшие пути одной операцией."""
        root = QFileDialog.getExistingDirectory(
            self._screen.window(),
            "Где теперь лежит библиотека? Выберите новый корень",
            self._settings.last_project_dir,
        )
        if not root:
            return job
        fixed_job, fixed, still_missing = relink_job(job, Path(root))
        lines: list[str] = []
        if fixed:
            lines.append("Починено:")
            lines.extend(f"✓ {text}" for text in fixed)
        if still_missing:
            lines.append("Так и не найдено (перевыберите вручную):")
            lines.extend(f"× {entry.label}: {entry.path}" for entry in still_missing)
        if not fixed and not still_missing:
            lines.append("Потерянных путей не осталось.")
        QMessageBox.information(self._screen, "Перепривязка путей", "\n".join(lines))
        if fixed:
            answer = QMessageBox.question(
                self._screen,
                "LoopStudio",
                "Сохранить исправленные пути в файл проекта?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                with contextlib.suppress(OSError):
                    fixed_job.save(project_path)
        return fixed_job

    def _warn_if_newer_schema(self, path: Path) -> None:
        """Проект из более новой версии: открываем, но честно предупреждаем."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if isinstance(version, int) and version > SCHEMA_VERSION:
            QMessageBox.warning(
                self._screen,
                "LoopStudio",
                "Этот проект создан более новой версией LoopStudio.\n"
                "Открою, что смогу; незнакомые настройки будут потеряны "
                "при сохранении. Лучше обновить LoopStudio.",
            )
