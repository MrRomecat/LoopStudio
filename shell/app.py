"""Лаунчер Studio Suite. В v1 сразу открывает LoopStudio."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QResizeEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMenu, QMessageBox

from core.applog import install_excepthook, log_file, setup_logging
from core.media.ffmpeg import app_root
from core.settings import AppSettings
from core.suite import AppContext
from core.ui import theme
from core.ui.widgets import apply_ui_scale
from modules.loopstudio.module import LoopStudioModule, LoopStudioWidget
from shell.activation import ensure_activated

APP_VERSION = "1.5.4"


def _show_crash(text: str) -> None:
    """Показать необработанную ошибку пользователю (раньше падения были немыми:
    запись в журнал есть, а на экране — просто «кнопка не сработала»)."""
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        return  # не главный поток — останется только запись в журнале
    QMessageBox.critical(
        None,
        "LoopStudio — ошибка",
        f"Произошла непредвиденная ошибка:\n{text}\n\n"
        "Работа продолжается. Подробности — в журнале приложения "
        "(меню Проект → «Открыть журнал приложения»).",
    )


def _app_icon() -> QIcon | None:
    """Фирменная иконка приложения (окно, панель задач, трей)."""
    for name in ("icon.ico", "icon_256.png"):
        path = app_root() / "assets" / name
        if path.is_file():
            return QIcon(str(path))
    return None


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.setWindowTitle(f"LoopStudio {APP_VERSION}")
        # форма окна: меньше минимума не сжимается (функции всегда видны),
        # но и не больше рабочей области экрана (ноутбуки 1366×768)
        avail_w, avail_h = 1920, 1080
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            avail_w, avail_h = available.width(), available.height()
        self.setMinimumSize(min(1180, avail_w - 20), min(760, avail_h - 60))
        self.resize(min(1240, avail_w - 40), min(920, avail_h - 40))
        # плавный масштаб интерфейса при растягивании (дебаунс на ресайз)
        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(120)
        self._scale_timer.timeout.connect(self._refresh_ui_scale)
        module = LoopStudioModule()
        widget = module.create_widget(ctx)
        assert isinstance(widget, LoopStudioWidget)
        self._module_widget = widget
        widget.version_label.setText(APP_VERSION)  # версия — в шапке экрана
        self.setCentralWidget(widget)
        self._build_menu()
        widget.controller.project_state_changed.connect(self._on_project_state)
        widget.controller.notify_project_state()

    def _build_menu(self) -> None:
        project_menu = self.menuBar().addMenu("Проект")
        open_action = QAction("Открыть проект…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_project)
        save_action = QAction("Сохранить", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)
        save_as_action = QAction("Сохранить как…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project_as)
        self._recent_menu = QMenu("Недавние проекты", self)
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        quick_setup_action = QAction("Быстрая настройка видео…", self)
        quick_setup_action.setToolTip("Собрать ролик за 4 вопроса — можно и с демо-материалами")
        quick_setup_action.triggered.connect(self._module_widget.controller.run_quick_setup)
        log_action = QAction("Открыть журнал приложения", self)
        log_action.setToolTip("Полная история работы и ошибок — для разбора ночных сбоев")
        log_action.triggered.connect(self._open_app_log)
        quit_action = QAction("Выход", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        project_menu.addAction(open_action)
        project_menu.addMenu(self._recent_menu)
        project_menu.addSeparator()
        project_menu.addAction(save_action)
        project_menu.addAction(save_as_action)
        project_menu.addSeparator()
        project_menu.addAction(quick_setup_action)
        project_menu.addAction(log_action)
        project_menu.addSeparator()
        project_menu.addAction(quit_action)

    def _on_project_state(self, suffix: str) -> None:
        """Заголовок: «LoopStudio 1.2.0 — Мой канал *» (звёздочка = не сохранено)."""
        self.setWindowTitle(f"LoopStudio {APP_VERSION}{suffix}")

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Ресайз окна перезапускает дебаунс пересчёта масштаба (Qt override)."""
        super().resizeEvent(event)
        self._scale_timer.start()

    def _refresh_ui_scale(self) -> None:
        """Широкое окно — крупнее интерфейс (до +30%), чтобы не было пусто."""
        scale = max(1.0, min(1.3, self.width() / 1280))
        if abs(scale - theme.UI_SCALE) < 0.03:
            return
        apply_ui_scale(scale)
        self._module_widget.set_ui_scale(scale)

    def _fill_recent_menu(self) -> None:
        # родитель QAction — само меню: clear() тогда реально удаляет действия,
        # а не копит их на окне при каждом открытии меню
        self._recent_menu.clear()
        recents = self._ctx.settings.recent_projects
        if not recents:
            empty = QAction("(пусто)", self._recent_menu)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for raw in recents:
            path = Path(raw)
            action = QAction(path.stem, self._recent_menu)
            action.setToolTip(raw)
            if path.is_file():
                action.triggered.connect(
                    lambda checked=False, p=path: self._module_widget.controller.open_project(p)
                )
            else:
                action.setText(f"{path.stem} (файл не найден)")
                action.setEnabled(False)
            self._recent_menu.addAction(action)
        self._recent_menu.addSeparator()
        clear_action = QAction("Очистить список", self._recent_menu)
        clear_action.triggered.connect(self._clear_recents)
        self._recent_menu.addAction(clear_action)

    def _clear_recents(self) -> None:
        self._ctx.settings.recent_projects = []
        self._ctx.settings.save()

    def _open_app_log(self) -> None:
        import os

        path = log_file()
        if path.is_file():
            os.startfile(str(path))

    def _project_dir(self) -> str:
        return self._ctx.settings.last_project_dir or str(Path.home())

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть проект", self._project_dir(), "Проект LoopStudio (*.lsproj)"
        )
        if path:
            self._module_widget.controller.open_project(Path(path))

    def _save_project(self) -> None:
        """Ctrl+S: тихо в открытый проект; если проекта нет — «Сохранить как»."""
        if not self._module_widget.controller.save_current():
            self._save_project_as()

    def _save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить проект", self._project_dir(), "Проект LoopStudio (*.lsproj)"
        )
        if path:
            if not path.endswith(".lsproj"):
                path += ".lsproj"
            self._module_widget.controller.save_project(Path(path))

    def closeEvent(self, event: QCloseEvent) -> None:
        # при активной очереди — честный вопрос: сохранить задания или отменить
        if not self._module_widget.controller.request_close():
            event.ignore()
            return
        self._module_widget.controller.shutdown()
        event.accept()


def main() -> int:
    setup_logging(APP_VERSION)
    install_excepthook(_show_crash)
    app = QApplication(sys.argv)
    app.setApplicationName("LoopStudio")
    app.setApplicationVersion(APP_VERSION)
    theme.load_fonts()  # вшитые Lora/Manrope/JetBrains Mono (если уже скачаны)
    app.setStyleSheet(theme.STYLESHEET)
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    if ensure_activated() is None:
        return 0  # нет лицензии — основной экран не открывается
    ctx = AppContext(settings=AppSettings.load())
    window = MainWindow(ctx)
    window.show()
    return app.exec()
