"""Диалоги LoopStudio: пре-флайт, итоги очереди, отсчёт выключения,
пакетный режим, проблемы открытого проекта."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.assets import LICENSE_NOTE, BuiltinAsset
from core.render.batch import list_music_subfolders
from core.render.preflight import PreflightReport
from core.render.project_check import ProjectCheck
from core.render.queue import QueueItem
from core.ui import theme
from core.ui.widgets import FilePicker, VinylDisc, dot_icon


def _dialog_header(dialog: QDialog, title: str) -> QVBoxLayout:
    """Фирменная шапка модалки: мини-пластинка + заголовок Lora + «×» + линия."""
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(16, 12, 16, 14)
    outer.setSpacing(10)
    head = QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(VinylDisc(18))
    caption = QLabel(title)
    caption.setObjectName("DialogTitle")
    head.addWidget(caption)
    head.addStretch(1)
    close_btn = QPushButton("×")
    close_btn.setFixedSize(26, 26)
    close_btn.setStyleSheet("font-size: 15px; font-weight: 600; padding: 0;")
    close_btn.setToolTip("Закрыть окно")
    close_btn.clicked.connect(dialog.reject)
    head.addWidget(close_btn)
    outer.addLayout(head)
    rule = QFrame()
    rule.setObjectName("DialogRule")
    outer.addWidget(rule)
    return outer


class NameDialog(QDialog):
    """Ввод имени (профиль канала, пресет микса) в фирменном стиле."""

    def __init__(self, parent: QWidget | None, *, title: str, hint: str, initial: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        layout = _dialog_header(self, title)

        hint_label = QLabel(hint)
        hint_label.setObjectName("Hint")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
        self._edit = QLineEdit(initial)
        layout.addWidget(self._edit)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("GhostBack")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Сохранить")
        save_btn.setObjectName("Primary")
        theme.apply_button_shadow(save_btn)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)
        self._edit.returnPressed.connect(self.accept)
        self._edit.setFocus()

    @staticmethod
    def get_text(parent: QWidget | None, *, title: str, hint: str, initial: str = "") -> str:
        """Показать диалог; пустая строка = отмена."""
        dialog = NameDialog(parent, title=title, hint=hint, initial=initial)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return dialog._edit.text().strip()


class ContinueSessionDialog(QDialog):
    """«Продолжить, где остановились?» — возврат настроек прошлого сеанса."""

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LoopStudio")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(VinylDisc(30), 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        caption = QLabel("Продолжить, где остановились?")
        caption.setObjectName("DialogTitle")
        text_col.addWidget(caption)
        body = QLabel("Вернуть настройки из прошлого сеанса. Автосохранение — раз в 45 секунд.")
        body.setObjectName("Muted")
        body.setWordWrap(True)
        text_col.addWidget(body)
        head.addLayout(text_col, 1)
        layout.addLayout(head)

        buttons = QHBoxLayout()
        continue_btn = QPushButton("Продолжить")
        continue_btn.setObjectName("Primary")
        theme.apply_button_shadow(continue_btn)
        continue_btn.setDefault(True)
        continue_btn.clicked.connect(self.accept)
        fresh_btn = QPushButton("Начать заново")
        fresh_btn.setObjectName("GhostBack")
        fresh_btn.clicked.connect(self.reject)
        buttons.addWidget(continue_btn)
        buttons.addWidget(fresh_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)


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


class PreflightDialog(QDialog):
    """Итог пре-флайта перед постановкой в очередь.

    Показывается только если есть предупреждения или блокеры. Решение всегда
    за пользователем: смета приблизительная, «Всё равно рендерить» доступно.
    """

    def __init__(self, parent: QWidget | None, report: PreflightReport) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка перед рендером")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(420)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setSpacing(8)

        # только глифы, которые есть в любом шрифте: эмодзи-знаки (⛔ ℹ)
        # рисуются цветными картинками или пустыми квадратами
        for text in report.blockers:
            rows.addWidget(self._line(f"Нельзя: {text}", "#e05c5c"))
        for text in report.warnings:
            rows.addWidget(self._line(f"⚠ {text}", "#d9a441"))
        for text in report.infos:
            rows.addWidget(self._line(f"· {text}", None))
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        hint = QLabel(
            "Оценки места и длительности приблизительные. "
            "Решение за вами: рендер можно запустить в любом случае."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox()
        render_btn = buttons.addButton(
            "Всё равно рендерить", QDialogButtonBox.ButtonRole.AcceptRole
        )
        back_btn = buttons.addButton(
            "Вернуться к настройкам", QDialogButtonBox.ButtonRole.RejectRole
        )
        if report.blockers:
            back_btn.setDefault(True)
        else:
            render_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _line(text: str, color: str | None) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        if color is not None:
            label.setStyleSheet(f"color: {color};")
        return label

    @staticmethod
    def confirm(parent: QWidget | None, report: PreflightReport) -> bool:
        """True = пользователь решил рендерить."""
        dialog = PreflightDialog(parent, report)
        return dialog.exec() == QDialog.DialogCode.Accepted


class QueueSummaryDialog(QDialog):
    """«Итоги очереди»: все задания, статусы, длительности, предупреждения.

    Двойной клик по готовой строке открывает ролик.
    """

    def __init__(self, parent: QWidget | None, items: list[QueueItem]) -> None:
        super().__init__(parent)
        done = sum(1 for i in items if i.status == "done")
        failed = sum(1 for i in items if i.status == "failed")
        self.setWindowTitle(f"Итоги очереди: {done} готово, {failed} с ошибкой")
        self.setMinimumSize(760, 380)
        self._items = items
        layout = QVBoxLayout(self)

        table = QTableWidget(len(items), 5)
        table.setHorizontalHeaderLabels(["№", "Задание", "Статус", "Рендер", "Заметки"])
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        for row, item in enumerate(items):
            notes: list[str] = []
            if item.error:
                notes.append(item.error.splitlines()[0])
            if item.result is not None:
                notes.extend(item.result.warnings)
            took = _mmss(item.duration_seconds) if item.duration_seconds is not None else ""
            kind = "Предпросмотр · " if item.preview else ""
            cells = [
                str(item.id),
                f"{kind}{item.title or 'Задание'}",
                _STATUS_LABELS.get(item.status, item.status),
                took,
                "; ".join(notes) if notes else "—",
            ]
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if item.result is not None:
                    cell.setToolTip(str(item.result.output_path))
                elif item.error:
                    cell.setToolTip(item.error)
                table.setItem(row, col, cell)
        table.cellDoubleClicked.connect(self._open_row)
        self._table = table
        layout.addWidget(table)

        hint = QLabel("Двойной клик по готовому заданию — открыть ролик.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _open_row(self, row: int, column: int) -> None:
        if 0 <= row < len(self._items):
            item = self._items[row]
            if item.status == "done" and item.result is not None:
                path = item.result.output_path
                if path.is_file():
                    os.startfile(str(path))


class BatchDialog(QDialog):
    """Пакетный режим: родительская папка → подпапки-плейлисты галочками.

    Каждая отмеченная подпапка станет отдельным заданием с текущей
    конфигурацией формы (свой плейлист, авто-порядок, {folder} в названии).
    """

    def __init__(self, parent: QWidget | None, initial_dir: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Несколько папок музыки — пакетный режим")
        self.setMinimumSize(560, 460)
        self._initial_dir = initial_dir
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Выберите родительскую папку: каждая её подпапка с треками — "
            "отдельный плейлист и отдельный ролик с текущими настройками формы.\n\n"
            "Здесь только выбор папок. Рендер запустится позже — кнопкой "
            "«Сгенерировать» в шаге 10, когда закончите все настройки."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self._dir_label = QLabel("Папка не выбрана")
        self._dir_label.setObjectName("Muted")
        pick_btn = QPushButton("Выбрать родительскую папку…")
        pick_btn.clicked.connect(self._pick_parent)
        pick_row.addWidget(pick_btn)
        pick_row.addWidget(self._dir_label, 1)
        layout.addLayout(pick_row)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)
        self._list.itemChanged.connect(lambda _item: self._refresh_summary())

        self._summary = QLabel("Выбрано папок: 0")
        layout.addWidget(self._summary)

        buttons = QDialogButtonBox()
        self._ok_btn = buttons.addButton("Выбрать папки", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        self._ok_btn.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _pick_parent(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Родительская папка с подборками музыки", self._initial_dir
        )
        if not path:
            return
        self._dir_label.setText(path)
        self._list.clear()
        try:
            folders = list_music_subfolders(Path(path))
        except NotADirectoryError:
            folders = []
        if not folders:
            self._summary.setText(
                "В выбранной папке нет подпапок с поддерживаемыми треками "
                "(wav/flac/mp3/m4a/ogg/opus/aac)."
            )
            self._ok_btn.setEnabled(False)
            return
        for folder in folders:
            item = QListWidgetItem(f"{folder.path.name} · треков: {folder.track_count}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, str(folder.path))
            self._list.addItem(item)
        self._refresh_summary()

    def _checked_paths(self) -> list[Path]:
        paths: list[Path] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                paths.append(Path(str(item.data(Qt.ItemDataRole.UserRole))))
        return paths

    def _refresh_summary(self) -> None:
        count = len(self._checked_paths())
        self._summary.setText(
            f"Выбрано папок: {count} — столько роликов поставит «Сгенерировать» (шаг 10)"
        )
        self._ok_btn.setEnabled(count > 0)

    def selected_folders(self) -> list[Path]:
        """Отмеченные папки (после accept)."""
        return self._checked_paths()


class ProjectIssuesDialog(QDialog):
    """Сводка проблем открытого проекта: потерянные пути и дифф плейлиста."""

    def __init__(self, parent: QWidget | None, check: ProjectCheck) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проект открыт с замечаниями")
        self.setMinimumWidth(600)
        self.relink_requested = False
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(400)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setSpacing(8)

        if check.missing:
            rows.addWidget(self._line("Не найдены файлы и папки проекта:", None))
            for entry in check.missing:
                rows.addWidget(self._line(f"× {entry.label}\n{entry.path}", "#e05c5c"))
            rows.addWidget(
                self._line(
                    "Если библиотека переехала (другой диск или папка) — нажмите "
                    "«Указать новый корень…» и выберите папку, где она лежит теперь: "
                    "пути починятся одной операцией.",
                    None,
                )
            )
        diff = check.playlist
        if not diff.empty:
            parts: list[str] = []
            if diff.missing:
                parts.append(
                    f"⚠ Треков из проекта не найдено: {len(diff.missing)} "
                    f"({', '.join(diff.missing[:5])}{'…' if len(diff.missing) > 5 else ''})"
                )
            if diff.added:
                parts.append(
                    f"⚠ Новых треков в папке: {len(diff.added)} — добавлены в конец "
                    f"и подсвечены в списке (шаг 2)"
                )
            rows.addWidget(self._line("Плейлист изменился со времени сохранения:", None))
            for text in parts:
                rows.addWidget(self._line(text, "#d9a441"))
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox()
        if check.missing:
            relink_btn = buttons.addButton(
                "Указать новый корень…", QDialogButtonBox.ButtonRole.ActionRole
            )
            relink_btn.clicked.connect(self._on_relink)
        open_btn = buttons.addButton("Открыть как есть", QDialogButtonBox.ButtonRole.AcceptRole)
        open_btn.setDefault(True)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_relink(self) -> None:
        self.relink_requested = True
        self.accept()

    @staticmethod
    def _line(text: str, color: str | None) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        if color is not None:
            label.setStyleSheet(f"color: {color};")
        return label


class AssetGalleryDialog(QDialog):
    """Галерея встроенных ассетов: список с выбором двойным кликом."""

    def __init__(self, parent: QWidget | None, title: str, items: list[BuiltinAsset]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(460, 420)
        self._items = items
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._list = QListWidget()
        for asset in items:
            item = QListWidgetItem(asset.name)
            item.setToolTip(str(asset.path))
            self._list.addItem(item)
        if items:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _i: self.accept())
        layout.addWidget(self._list, 1)

        note = QLabel(LICENSE_NOTE)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        pick = buttons.addButton("Выбрать", QDialogButtonBox.ButtonRole.AcceptRole)
        pick.setDefault(True)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_path(self) -> str:
        row = self._list.currentRow()
        if 0 <= row < len(self._items):
            return str(self._items[row].path)
        return ""


_QUICK_VIDEO_FILTER = "Видео (*.mp4 *.mov *.mkv *.avi *.webm)"
_QUICK_AUDIO_FILTER = "Аудио (*.wav *.flac *.mp3 *.m4a *.ogg *.opus *.aac)"


class QuickSetupDialog(QDialog):
    """«Быстрая настройка видео»: 4 шага — и сразу полноценная генерация.

    Ничего не настраивает «по своему усмотрению»: поля как есть переносятся
    в штатную форму, и рендер стартует тем же путём, что и кнопка
    «Сгенерировать» шага 10 (пре-флайт, очередь, прогресс — всё обычное).
    Интро/аутро/первый трек — необязательные дополнения.
    """

    def __init__(
        self,
        parent: QWidget | None,
        *,
        default_output: str = "",
        initial_video: str = "",
        initial_intro: str = "",
        initial_outro: str = "",
        initial_music: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Быстрая настройка видео")
        self.setMinimumWidth(720)
        layout = _dialog_header(self, "Быстрая настройка")

        layout.addWidget(QLabel("1. Видеофон (клип, который будет зациклен):"))
        self.video_picker = FilePicker("Видео (mp4/mov/mkv…)", name_filter=_QUICK_VIDEO_FILTER)
        if initial_video:
            self.video_picker.setPath(initial_video)
        layout.addWidget(self.video_picker)

        clips_row = QHBoxLayout()
        clips_row.setSpacing(10)
        intro_col = QVBoxLayout()
        intro_col.addWidget(QLabel("Интро (необязательно):"))
        self.intro_picker = FilePicker("Клип-вступление", name_filter=_QUICK_VIDEO_FILTER)
        if initial_intro:
            self.intro_picker.setPath(initial_intro)
        intro_col.addWidget(self.intro_picker)
        outro_col = QVBoxLayout()
        outro_col.addWidget(QLabel("Аутро (необязательно):"))
        self.outro_picker = FilePicker("Клип-концовка", name_filter=_QUICK_VIDEO_FILTER)
        if initial_outro:
            self.outro_picker.setPath(initial_outro)
        outro_col.addWidget(self.outro_picker)
        clips_row.addLayout(intro_col, 1)
        clips_row.addLayout(outro_col, 1)
        layout.addLayout(clips_row)

        layout.addWidget(QLabel("2. Папка с музыкой (треки станут плейлистом):"))
        self.music_picker = FilePicker("Папка с треками", directory=True)
        if initial_music:
            self.music_picker.setPath(initial_music)
        layout.addWidget(self.music_picker)

        layout.addWidget(QLabel("3. Первый трек (необязательно — с него начнётся ролик):"))
        self.first_track_picker = FilePicker(
            "Трек из папки музыки", name_filter=_QUICK_AUDIO_FILTER
        )
        # выбрал первый трек раньше папки — папка музыки подставится сама
        self.first_track_picker.pathChanged.connect(self._on_first_track_picked)
        layout.addWidget(self.first_track_picker)

        layout.addWidget(QLabel("4. Куда сохранить готовый ролик:"))
        self.output_picker = FilePicker("Папка сохранения", directory=True)
        if default_output:
            self.output_picker.setPath(default_output)
        layout.addWidget(self.output_picker)

        layout.addWidget(QLabel("Сколько должен идти ролик:"))
        self._durations = [
            ("30 минут", 30),
            ("1 час", 60),
            ("2 часа", 120),
            ("3 часа", 180),
            ("5 часов", 300),
            ("8 часов", 480),
            ("10 часов", 600),
        ]
        self.duration_minutes = 60
        choice_row = QHBoxLayout()
        self._duration_buttons: list[QPushButton] = []
        for label, minutes in self._durations:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, m=minutes: self._pick_duration(m))
            self._duration_buttons.append(btn)
            choice_row.addWidget(btn)
        self._duration_buttons[1].setChecked(True)
        choice_row.addStretch(1)
        layout.addLayout(choice_row)

        buttons = QHBoxLayout()
        skip = QPushButton("Пропустить — настрою вручную")
        skip.setObjectName("GhostBack")
        skip.setToolTip("Закрыть окно, ничего не запуская")
        skip.clicked.connect(self.reject)
        go = QPushButton("Сгенерировать")
        go.setObjectName("Primary")
        go.setIcon(dot_icon())
        theme.apply_button_shadow(go)
        go.setDefault(True)
        go.setToolTip(
            "Поставить полноценный ролик в очередь рендера — то же самое, "
            "что кнопка «Сгенерировать» на шаге «Рендер». Окно закроется, "
            "прогресс будет виден внизу формы."
        )
        go.clicked.connect(self._on_accept)
        buttons.addWidget(skip)
        buttons.addStretch(1)
        buttons.addWidget(go)
        layout.addLayout(buttons)

    def _pick_duration(self, minutes: int) -> None:
        self.duration_minutes = minutes
        for btn, (_label, m) in zip(self._duration_buttons, self._durations, strict=False):
            btn.setChecked(m == minutes)

    def _on_first_track_picked(self, path: str) -> None:
        if path and not self.music_picker.path():
            self.music_picker.setPath(str(Path(path).parent))

    def _on_accept(self) -> None:
        if not self.video_picker.path():
            QMessageBox.information(self, "LoopStudio", "Выберите видеофон (шаг 1).")
            return
        if not self.music_picker.path():
            QMessageBox.information(self, "LoopStudio", "Выберите папку с музыкой (шаг 2).")
            return
        if not self.output_picker.path():
            QMessageBox.information(self, "LoopStudio", "Выберите папку сохранения (шаг 4).")
            return
        first = self.first_track_picker.path()
        if first and Path(first).parent.resolve() != Path(self.music_picker.path()).resolve():
            QMessageBox.information(
                self,
                "LoopStudio",
                "Первый трек должен лежать в выбранной папке с музыкой (шаг 2): "
                "плейлист собирается из её файлов.",
            )
            return
        self.accept()


class ShutdownCountdownDialog(QDialog):
    """«Компьютер выключится через N секунд» с окном отмены."""

    def __init__(self, parent: QWidget | None, seconds: int = 60, *, summary: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Очередь завершена")
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._remaining = max(1, seconds)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if summary:
            summary_label = QLabel(summary)
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)

        self._label = QLabel()
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        buttons = QDialogButtonBox()
        now_btn = buttons.addButton("Выключить сейчас", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = buttons.addButton(
            "Отменить выключение", QDialogButtonBox.ButtonRole.RejectRole
        )
        cancel_btn.setDefault(True)
        now_btn.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        self._label.setText(
            f"Компьютер выключится через {self._remaining} с.\n"
            f"Незавершённых заданий в очереди нет; статусы сохранены в журнале."
        )

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.accept()
        else:
            self._refresh()

    def done(self, result: int) -> None:
        """Остановить таймер при любом закрытии (Qt override)."""
        self._timer.stop()
        super().done(result)
