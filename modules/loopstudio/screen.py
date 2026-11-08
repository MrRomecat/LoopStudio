"""Экран LoopStudio — мастер из 6 шагов в теме «Vinyl Warm».

Каркас: шапка (пластинка + профиль канала + «Быстрая настройка») → степпер
из шести дисков → заголовок шага → страница настроек → футер навигации.
Переход по шагам свободный (это настройки, а не строгий визард).

Экран — только форма: собирает/показывает RenderJob и излучает сигналы кнопок.
Всю работу (очередь, предпрослушка, файлы) делает controller.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from core.audio.playlist import scan_music_folder
from core.render.job import AmbientConfig, EffectsConfig, RenderJob
from core.settings import AppSettings
from core.ui import theme
from core.ui.widgets import (
    DarkPanel,
    FilePicker,
    InfoDot,
    LabeledSlider,
    Panel,
    PercentSlider,
    Stepper,
    Toggle,
    VinylDisc,
    dot_icon,
    dotted_rule,
)

VIDEO_FILTER = "Видео (*.mp4 *.mov *.mkv *.avi *.webm);;Все файлы (*)"
AUDIO_FILTER = "Аудио (*.wav *.flac *.mp3 *.m4a *.ogg *.opus *.aac);;Все файлы (*)"

_LUT_ITEMS: list[tuple[str, str]] = [
    ("Выключено", ""),
    ("Тёплый", "warm"),
    ("Холодный", "cold"),
    ("Ночной", "night"),
    ("VHS", "vhs"),
    ("Сепия", "sepia"),
]
_RESOLUTIONS: list[tuple[str, str]] = [
    ("720p", "720p"),
    ("1080p", "1080p"),
    ("1440p", "1440p"),
    ("4K", "2160p"),
]
_QUALITIES: list[tuple[str, str]] = [
    ("Как источник", "source"),
    ("Высокое", "high"),
    ("Стандарт", "standard"),
]
_DURATION_CHIPS: list[tuple[str, int]] = [
    ("30 мин", 30),
    ("1 ч", 60),
    ("2 ч", 120),
    ("3 ч", 180),
    ("5 ч", 300),
    ("8 ч", 480),
    ("10 ч", 600),
]

#: (заголовок, курсивный подзаголовок) каждого шага мастера
_STEPS: list[tuple[str, str]] = [
    ("Видео-источник", "клип, который будет зациклен на всю длительность"),
    ("Музыка", "папка с треками, порядок и обработка звука"),
    ("Звуковой микс", "музыка + до трёх эмбиент-слоёв"),
    ("Зацикливание и эффекты", "бесшовность лупа и киношный вид"),
    ("Экспорт", "как и куда сохранить готовый ролик"),
    ("Генерация", "рендер и очередь заданий"),
]
_STEPPER_LABELS = ["Видео", "Музыка", "Микс", "Эффекты", "Экспорт", "Рендер"]
_CONTENT_MAX_WIDTH = 1120

# роли данных строк очереди (пишет контроллер, рисует QueueRowDelegate)
QUEUE_ROLE_ID = int(Qt.ItemDataRole.UserRole)
QUEUE_ROLE_STATUS = QUEUE_ROLE_ID + 1
QUEUE_ROLE_TITLE = QUEUE_ROLE_ID + 2
QUEUE_ROLE_META = QUEUE_ROLE_ID + 3
QUEUE_ROLE_BADGE = QUEUE_ROLE_ID + 4

#: статус → (цвет точки, цвет бейджа, фон бейджа)
_QUEUE_STATUS_COLORS: dict[str, tuple[str, str, str]] = {
    "pending": (theme.TEXT_GHOST, theme.STATUS_CANCEL, theme.STATUS_CANCEL_BG),
    "running": (theme.ACCENT, theme.STATUS_RENDER, theme.STATUS_RENDER_BG),
    "done": (theme.INK, theme.STATUS_DONE, theme.STATUS_DONE_BG),
    "failed": (theme.DESTRUCTIVE, theme.STATUS_RENDER, theme.STATUS_RENDER_BG),
    "cancelled": (theme.STATUS_CANCEL, theme.STATUS_CANCEL, theme.STATUS_CANCEL_BG),
}


class QueueRowDelegate(QStyledItemDelegate):
    """Строка очереди в стиле макета: №, точка статуса, имя, мета, бейдж."""

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(option.rect.width(), theme.scaled(40))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        status = str(index.data(QUEUE_ROLE_STATUS) or "pending")
        title = str(index.data(QUEUE_ROLE_TITLE) or "")
        meta = str(index.data(QUEUE_ROLE_META) or "")
        badge = str(index.data(QUEUE_ROLE_BADGE) or "")
        item_id = index.data(QUEUE_ROLE_ID)
        dot_color, badge_fg, badge_bg = _QUEUE_STATUS_COLORS.get(
            status, _QUEUE_STATUS_COLORS["pending"]
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if status == "running":
            painter.fillRect(rect, QColor("#fdf8ee"))
        elif option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(theme.FIELD_SELECTED))
        # пунктирный разделитель понизу
        painter.setPen(QColor(theme.DOTTED))
        y_bottom = rect.bottom()
        for x in range(rect.left() + 6, rect.right() - 6, 5):
            painter.drawPoint(x, y_bottom)

        scale = theme.UI_SCALE
        x = rect.left() + round(10 * scale)
        center_y = rect.center().y()

        mono = QFont(option.font)
        mono.setFamilies(["JetBrains Mono", "Consolas", "monospace"])
        mono.setPointSizeF(8.5 * scale)
        painter.setFont(mono)
        painter.setPen(QColor(theme.TEXT_GHOST))
        number = f"№{item_id}" if item_id is not None else ""
        painter.drawText(
            QRect(x, rect.top(), round(36 * scale), rect.height()),
            Qt.AlignmentFlag.AlignVCenter,
            number,
        )
        x += round(40 * scale)

        dot = round(8 * scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(dot_color))
        painter.drawEllipse(QRect(x, center_y - dot // 2, dot, dot))
        x += round(16 * scale)

        badge_width = 0
        if badge:
            badge_font = QFont(option.font)
            badge_font.setFamilies(["JetBrains Mono", "Consolas", "monospace"])
            badge_font.setPointSizeF(8.0 * scale)
            badge_font.setBold(True)
            painter.setFont(badge_font)
            metrics_width = painter.fontMetrics().horizontalAdvance(badge)
            badge_width = metrics_width + round(16 * scale)
            badge_height = round(20 * scale)
            badge_rect = QRect(
                rect.right() - badge_width - round(10 * scale),
                center_y - badge_height // 2,
                badge_width,
                badge_height,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(badge_bg))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QColor(badge_fg))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)

        text_right = rect.right() - badge_width - round(20 * scale)
        serif = QFont(option.font)
        serif.setFamilies(["Lora", "Georgia", "serif"])
        serif.setPointSizeF(10.0 * scale)
        serif.setBold(True)
        painter.setFont(serif)
        painter.setPen(QColor(theme.TEXT if status != "cancelled" else theme.TEXT_FAINT))
        title_rect = QRect(x, rect.top(), max(60, text_right - x), rect.height())
        title_text = painter.fontMetrics().elidedText(
            title, Qt.TextElideMode.ElideRight, round(title_rect.width() * 0.55)
        )
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title_text)

        if meta:
            meta_x = x + round(title_rect.width() * 0.55) + round(12 * scale)
            meta_font = QFont(option.font)
            meta_font.setPointSizeF(8.5 * scale)
            painter.setFont(meta_font)
            painter.setPen(QColor(theme.TEXT_FAINT))
            meta_rect = QRect(meta_x, rect.top(), max(40, text_right - meta_x), rect.height())
            meta_text = painter.fontMetrics().elidedText(
                meta, Qt.TextElideMode.ElideRight, meta_rect.width()
            )
            painter.drawText(meta_rect, Qt.AlignmentFlag.AlignVCenter, meta_text)
        painter.restore()


class AmbientRow(QWidget):
    """Один эмбиент-слой: тумблер, файл, встроенные, ▶ прослушать, громкость."""

    play_requested = Signal(str, float)  # (путь, громкость слоя 0..1)
    builtin_requested = Signal(object)  # -> AmbientRow (открыть галерею для этой строки)

    def __init__(self, index: int, parent: QWidget | None = None, *, info_tip: str = "") -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.toggle = Toggle()
        self.toggle.setToolTip("Включить слой в микс")
        self.picker = FilePicker(f"Эмбиент-слой {index}", name_filter=AUDIO_FILTER)
        self.builtin = QPushButton("Встроенные…")
        self.builtin.setToolTip("Выбрать звук из встроенной библиотеки LoopStudio")
        self.play = QPushButton("▶")
        self.play.setFixedWidth(34)
        self.play.setToolTip("Прослушать (повторный клик — стоп)")
        self.volume = PercentSlider()
        self.volume.setValue(50)
        self.volume.setEnabled(False)  # громкость активна только у включённого слоя
        if info_tip:
            layout.addWidget(InfoDot(info_tip))
        layout.addWidget(self.toggle)
        layout.addWidget(self.picker, 3)
        layout.addWidget(self.builtin)
        layout.addWidget(self.play)
        layout.addWidget(self.volume, 2)
        # прослушивание с громкостью слайдера: баланс слышен без пересборки микса
        self.play.clicked.connect(
            lambda: self.play_requested.emit(self.picker.path(), self.volume.fraction())
        )
        self.builtin.clicked.connect(lambda: self.builtin_requested.emit(self))
        self.toggle.toggled.connect(self.volume.setEnabled)

    def set_source(self, path: str) -> None:
        """Выбрать звук слоя и сразу включить его в микс."""
        self.picker.setPath(path)
        self.toggle.setChecked(True)

    def config(self) -> AmbientConfig:
        return AmbientConfig(
            enabled=self.toggle.isChecked(), path=self.picker.path(), volume=self.volume.fraction()
        )

    def apply(self, config: AmbientConfig) -> None:
        self.toggle.setChecked(config.enabled)
        self.picker.setPath(config.path)
        self.volume.setFraction(config.volume)


class LoopStudioScreen(QWidget):
    """Мастер из 6 шагов. collect_job()/apply_job() — контракт с контроллером."""

    generate_clicked = Signal()
    preview_clicked = Signal()
    open_folder_clicked = Signal()
    listen_mix_clicked = Signal()
    cancel_clicked = Signal()
    save_mix_preset_clicked = Signal()
    delete_mix_preset_clicked = Signal()
    mix_preset_selected = Signal(str)
    save_profile_clicked = Signal()
    delete_profile_clicked = Signal()
    profile_selected = Signal(str)
    quick_setup_clicked = Signal()
    batch_clicked = Signal()
    ambient_play_requested = Signal(str, float)
    ambient_builtin_requested = Signal(object)
    overlay_builtin_clicked = Signal()
    shuffle_clicked = Signal()
    lint_clicked = Signal()
    stop_mix_clicked = Signal()
    preview_start_clicked = Signal()
    seams_clicked = Signal()
    batch_clear_clicked = Signal()

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        rule = QFrame()
        rule.setObjectName("HeaderRule")
        root.addWidget(rule)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        outer = QWidget()
        scroll.setWidget(outer)
        outer_row = QHBoxLayout(outer)
        outer_row.setContentsMargins(20, 8, 20, 16)
        self._content = QWidget()
        self._content.setMaximumWidth(_CONTENT_MAX_WIDTH)
        outer_row.addStretch(1)
        outer_row.addWidget(self._content, 1)
        outer_row.addStretch(1)

        column = QVBoxLayout(self._content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        self.stepper = Stepper(_STEPPER_LABELS)
        self.stepper.step_clicked.connect(self.go_to_step)
        column.addWidget(self.stepper)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        self._step_title = QLabel()
        self._step_title.setObjectName("StepTitle")
        self._step_subtitle = QLabel()
        self._step_subtitle.setObjectName("StepSubtitle")
        title_row.addWidget(self._step_title)
        title_row.addWidget(self._step_subtitle, 1, Qt.AlignmentFlag.AlignBottom)
        column.addLayout(title_row)

        self._stack = QStackedWidget()
        self.pages: list[QWidget] = [
            self._build_page_video(),
            self._build_page_music(),
            self._build_page_mix(),
            self._build_page_effects(),
            self._build_page_export(),
            self._build_page_render(),
        ]
        for page in self.pages:
            self._stack.addWidget(page)
        column.addWidget(self._stack)

        column.addLayout(self._build_footer())
        column.addStretch(1)

        self.go_to_step(0)

    # --- каркас: шапка, футер, навигация --------------------------------------

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("AppHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(10)

        row.addWidget(VinylDisc(26))
        title = QLabel("LoopStudio")
        title.setObjectName("AppTitle")
        row.addWidget(title)
        self.version_label = QLabel()  # заполняет shell (версия всегда на виду)
        self.version_label.setObjectName("AppVersion")
        row.addWidget(self.version_label)
        row.addStretch(1)

        chip = QFrame()
        chip.setObjectName("ProfileChip")
        chip_row = QHBoxLayout(chip)
        chip_row.setContentsMargins(12, 3, 6, 3)
        chip_row.setSpacing(6)
        chip_row.addWidget(
            InfoDot(
                "Профиль канала запоминает всю форму, кроме папки музыки и "
                "длительности: интро, видеофон, микс, эффекты, качество, папку "
                "сохранения, шаблон названия. Выбор в списке сразу применяет профиль."
            )
        )
        profile_caption = QLabel("Профиль:")
        chip_row.addWidget(profile_caption)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(150)
        chip_row.addWidget(self.profile_combo)
        self.save_profile_btn = QPushButton("Сохранить")
        self.save_profile_btn.setObjectName("PillLight")
        self.save_profile_btn.setToolTip("Запомнить текущую форму как профиль канала")
        chip_row.addWidget(self.save_profile_btn)
        self.delete_profile_btn = QPushButton("Удалить")
        self.delete_profile_btn.setObjectName("TextDanger")
        self.delete_profile_btn.setToolTip("Удалить выбранный профиль (с подтверждением)")
        chip_row.addWidget(self.delete_profile_btn)
        row.addWidget(chip)

        self.quick_setup_btn = QPushButton("Быстрая настройка")
        self.quick_setup_btn.setObjectName("PillDark")
        self.quick_setup_btn.setIcon(dot_icon())
        self.quick_setup_btn.setToolTip(
            "Собрать ролик за 4 шага: видеофон, папка с музыкой, длительность "
            "и куда сохранить — и сразу полноценная генерация."
        )
        row.addWidget(self.quick_setup_btn)

        self.save_profile_btn.clicked.connect(self.save_profile_clicked)
        self.delete_profile_btn.clicked.connect(self.delete_profile_clicked)
        self.quick_setup_btn.clicked.connect(self.quick_setup_clicked)
        self.profile_combo.textActivated.connect(self.profile_selected)
        return header

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.back_btn = QPushButton()
        self.back_btn.setObjectName("GhostBack")
        self.back_btn.setToolTip("Вернуться к предыдущему шагу")
        self.back_btn.clicked.connect(lambda: self.go_to_step(self.stepper.current() - 1))
        footer.addWidget(self.back_btn)
        footer.addStretch(1)
        self._step_counter = QLabel()
        self._step_counter.setObjectName("StepCounter")
        footer.addWidget(self._step_counter)
        self.continue_btn = QPushButton("Продолжить ›")
        self.continue_btn.setObjectName("Primary")
        theme.apply_button_shadow(self.continue_btn)
        self.continue_btn.setToolTip("Перейти к следующему шагу")
        self.continue_btn.clicked.connect(lambda: self.go_to_step(self.stepper.current() + 1))
        footer.addWidget(self.continue_btn)
        return footer

    def go_to_step(self, index: int) -> None:
        """Показать шаг мастера (0..5); переход свободный, клик по степперу."""
        index = max(0, min(len(self.pages) - 1, index))
        self._stack.setCurrentIndex(index)
        self.stepper.set_current(index)
        title, subtitle = _STEPS[index]
        self._step_title.setText(title)
        self._step_subtitle.setText(subtitle)
        self._step_counter.setText(f"шаг {index + 1} из {len(self.pages)}")
        self.back_btn.setVisible(index > 0)
        if index > 0:
            self.back_btn.setText(f"‹ {_STEPPER_LABELS[index - 1]}")
        self.continue_btn.setVisible(index < len(self.pages) - 1)
        last_before_render = index == len(self.pages) - 2
        self.continue_btn.setText("К генерации ›" if last_before_render else "Продолжить ›")

    def current_step(self) -> int:
        return self.stepper.current()

    def set_ui_scale(self, scale: float) -> None:
        """Подстроить экран под масштаб интерфейса (зовёт окно при ресайзе)."""
        self._content.setMaximumWidth(round(_CONTENT_MAX_WIDTH * scale))
        # моно-шрифт поля растёт вместе с масштабом — фикс-ширина обрезала «01:00»
        self.duration_edit.setFixedWidth(round(74 * scale))
        self.queue_list.doItemsLayout()  # делегат пересчитает высоту строк

    # --- утилиты вёрстки --------------------------------------------------------

    def _info_row(self, tip: str, widget: QWidget) -> QHBoxLayout:
        """Строка «ⓘ + виджет» — стандарт подсказок проекта."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(InfoDot(tip))
        row.addWidget(widget, 1)
        return row

    @staticmethod
    def _page(*panels: QWidget) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        for panel in panels:
            box.addWidget(panel)
        box.addStretch(1)
        return page

    @staticmethod
    def _labeled(text: str) -> QLabel:
        label = QLabel(text)
        label.setMinimumWidth(84)
        return label

    # --- шаг 1 · Видео ----------------------------------------------------------

    def _build_page_video(self) -> QWidget:
        panel = Panel()
        self.video_picker = FilePicker("Основное видео (5 сек – 5 ч)", name_filter=VIDEO_FILTER)
        self.video_info = QLabel()
        self.video_info.setObjectName("Mono")
        self.video_info.setWordWrap(True)
        self.video_info.hide()  # заполняет контроллер после фонового анализа файла
        self.intro_picker = FilePicker(
            "Интро — один раз в начале (необязательно)", name_filter=VIDEO_FILTER
        )
        self.outro_picker = FilePicker(
            "Аутро — один раз в конце (необязательно)", name_filter=VIDEO_FILTER
        )

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._labeled("Основное"))
        row.addWidget(
            InfoDot(
                "Видеофон ролика: этот файл зацикливается на всю длительность. "
                "Подойдёт клип от 5 секунд до 5 часов; лучше без резких склеек внутри."
            )
        )
        row.addWidget(self.video_picker, 1)
        panel.body.addLayout(row)
        panel.body.addWidget(self.video_info)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._labeled("Интро"))
        row.addWidget(
            InfoDot(
                "Проигрывается один раз в самом начале, до лупа (заставка канала). "
                "Время интро входит в общую длительность ролика."
            )
        )
        row.addWidget(self.intro_picker, 1)
        panel.body.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._labeled("Аутро"))
        row.addWidget(
            InfoDot("Проигрывается один раз в самом конце, после лупа (концовка канала).")
        )
        row.addWidget(self.outro_picker, 1)
        panel.body.addLayout(row)

        panel.body.addWidget(dotted_rule())

        self.source_audio_toggle = Toggle()
        self.source_audio_volume = PercentSlider()
        self.source_audio_volume.setValue(50)
        self.source_audio_volume.setEnabled(False)  # активен только при включённом тумблере
        self.source_audio_toggle.toggled.connect(self.source_audio_volume.setEnabled)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Подмешивает звук самого видеофона (волны, дождь, костёр) под музыку. "
                "Лупится вместе с видео и стартует после интро. Ползунок — громкость."
            )
        )
        row.addWidget(self.source_audio_toggle)
        row.addWidget(QLabel("Оригинальный звук видео"))
        row.addWidget(self.source_audio_volume, 1)
        panel.body.addLayout(row)
        return self._page(panel)

    # --- шаг 2 · Музыка ---------------------------------------------------------

    def _build_page_music(self) -> QWidget:
        top = Panel()
        self.music_picker = FilePicker(
            "Папка с треками (wav/flac/mp3/m4a/ogg/opus/aac)", directory=True
        )
        self.batch_btn = QPushButton("Несколько папок…")
        self.batch_btn.setToolTip(
            "Пакетный режим: выбрать несколько папок музыки — по ролику на каждую "
            "с текущими настройками. Рендер запустится кнопкой «Сгенерировать» (шаг 6)."
        )
        self.batch_clear_btn = QPushButton("×")
        self.batch_clear_btn.setFixedWidth(30)
        self.batch_clear_btn.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px;")
        self.batch_clear_btn.setToolTip("Отменить пакетный режим (вернуться к одной папке)")
        self.batch_clear_btn.hide()
        music_row = QHBoxLayout()
        music_row.setSpacing(8)
        music_row.addWidget(
            InfoDot(
                "Все поддерживаемые аудиофайлы папки станут плейлистом ролика "
                "(подпапки не сканируются). Либо нажмите «Несколько папок…» — "
                "тогда каждая выбранная папка станет отдельным роликом."
            )
        )
        music_row.addWidget(self.music_picker, 1)
        music_row.addWidget(self.batch_btn)
        music_row.addWidget(self.batch_clear_btn)
        top.body.addLayout(music_row)
        hint = QLabel(
            "Числовые имена — первыми по возрастанию (2 < 10), остальные — по имени. "
            "Порядок можно перетащить вручную; снятая галочка исключает трек из ролика."
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        top.body.addWidget(hint)

        # левая колонка: плейлист «Сторона A»
        playlist = Panel()
        head = QHBoxLayout()
        head.setSpacing(8)
        self.track_counter = QLabel()
        self.track_counter.setObjectName("PanelTitle")
        head.addWidget(self.track_counter)
        head.addStretch(1)
        self.shuffle_btn = QPushButton("⇄ Перемешать")
        self.shuffle_btn.setToolTip(
            "Случайный порядок с правилом «не два трека одного артиста подряд» "
            "(артист берётся из тегов файлов). Честное разнообразие переизданий."
        )
        self.lint_btn = QPushButton("Анализ громкости")
        self.lint_btn.setToolTip(
            "Замер каждого трека (LUFS): подсветит треки заметно громче или тише "
            "остальных и возможный клиппинг. Занимает до пары минут на большой пак."
        )
        head.addWidget(self.shuffle_btn)
        head.addWidget(self.lint_btn)
        playlist.body.addLayout(head)

        self.track_list = QListWidget()
        self.track_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.track_list.setMinimumHeight(220)
        playlist.body.addWidget(self.track_list)

        keep_row = QHBoxLayout()
        keep_row.setSpacing(8)
        self.shuffle_keep_first = Toggle()
        keep_row.addWidget(self.shuffle_keep_first)
        keep_row.addWidget(QLabel("Не трогать первый трек"))
        keep_row.addWidget(
            InfoDot(
                "При перемешивании первый трек останется на месте: сильное начало "
                "решает удержание первых 30 секунд."
            )
        )
        keep_row.addStretch(1)
        playlist.body.addLayout(keep_row)
        self.lint_summary = QLabel()
        self.lint_summary.setObjectName("Muted")
        self.lint_summary.setWordWrap(True)
        self.lint_summary.hide()
        playlist.body.addWidget(self.lint_summary)

        # правая колонка: тёмная панель «Переходы» + «Обработка звука»
        transitions = DarkPanel("Переходы")
        self.fade_out = LabeledSlider(0, 10, step=0.5, suffix=" с")
        self.pause = LabeledSlider(0, 5, step=0.5, suffix=" с")
        self.crossfade = LabeledSlider(0, 10, step=0.5, suffix=" с")
        for tip, caption, slider in (
            (
                "Плавное затухание в конце каждого трека. При включённом кроссфейде "
                "затухает только последний трек ролика (остальные перетекают сами). "
                "0 — выключено.",
                "Fade-out конца трека",
                self.fade_out,
            ),
            (
                "Тишина между треками — в ней продолжает звучать эмбиент. "
                "Пауза отключает кроссфейд: трекам, разделённым тишиной, нечего перекрывать.",
                "Пауза между треками",
                self.pause,
            ),
            (
                "Плавное перетекание конца одного трека в начало следующего. "
                "Работает только при паузе 0.",
                "Кроссфейд",
                self.crossfade,
            ),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(InfoDot(tip))
            caption_label = QLabel(caption)
            caption_label.setMinimumWidth(120)
            caption_label.setWordWrap(True)
            row.addWidget(caption_label)
            row.addWidget(slider, 1)
            transitions.body.addLayout(row)

        processing = Panel("Обработка звука")
        self.trim_silence_toggle = Toggle()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.trim_silence_toggle)
        row.addWidget(QLabel("Обрезать тишину в начале и конце"))
        row.addWidget(
            InfoDot(
                "Паки от артистов часто приходят с 2–5 секундами тишины в хвостах — "
                "кроссфейды уходят «в тишину». Обрезка происходит при рендере, "
                "исходные файлы не меняются."
            )
        )
        row.addStretch(1)
        processing.body.addLayout(row)

        self.normalize_toggle = Toggle()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.normalize_toggle)
        row.addWidget(QLabel("Нормализовать (−14 LUFS, YouTube)"))
        row.addWidget(
            InfoDot(
                "Приводит каждый трек к −14 LUFS — стандарту громкости YouTube. "
                "Убирает скачки между тихими и громкими треками сборника."
            )
        )
        row.addStretch(1)
        processing.body.addLayout(row)

        self.normalize_precise_toggle = Toggle()
        self.normalize_precise_toggle.setEnabled(False)  # активна при включённой нормализации
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.normalize_precise_toggle)
        row.addWidget(QLabel("Точная нормализация (2 прохода)"))
        row.addWidget(
            InfoDot(
                "Сначала замеряет каждый трек, затем выравнивает одним точным "
                "коэффициентом — без «дыхания» громкости на динамичной музыке "
                "(джаз, классика). Рендер дольше на минуты. Работает при включённой "
                "нормализации выше."
            )
        )
        row.addStretch(1)
        processing.body.addLayout(row)
        self.normalize_toggle.toggled.connect(self.normalize_precise_toggle.setEnabled)

        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(16)
        right_box.addWidget(transitions)
        right_box.addWidget(processing)
        right_box.addStretch(1)

        columns = QWidget()
        columns_row = QHBoxLayout(columns)
        columns_row.setContentsMargins(0, 0, 0, 0)
        columns_row.setSpacing(16)
        columns_row.addWidget(playlist, 5)
        columns_row.addWidget(right, 4)

        self.music_picker.pathChanged.connect(self._reload_tracks)
        self.track_list.itemChanged.connect(self._refresh_track_counter)
        self.track_list.model().rowsMoved.connect(lambda *_args: self._refresh_track_counter())
        self.shuffle_btn.clicked.connect(self.shuffle_clicked)
        self.lint_btn.clicked.connect(self.lint_clicked)
        self.batch_btn.clicked.connect(self.batch_clicked)
        self.batch_clear_btn.clicked.connect(self.batch_clear_clicked)
        self._refresh_track_counter()
        return self._page(top, columns)

    # --- шаг 3 · Микс -----------------------------------------------------------

    def _build_page_mix(self) -> QWidget:
        console = DarkPanel("Mixing Console")
        self.listen_mix_btn = QPushButton("▶ Прослушать микс")
        self.listen_mix_btn.setToolTip(
            "Соберёт и проиграет превью звучания (музыка + включённые слои, ~30 секунд)."
        )
        # «■» (чёрный квадрат) есть в любом шрифте — универсальный знак «стоп»
        self.stop_mix_btn = QPushButton("■")
        self.stop_mix_btn.setFixedWidth(34)
        self.stop_mix_btn.setToolTip("Остановить воспроизведение")
        console.header_row.addWidget(self.listen_mix_btn)
        console.header_row.addWidget(self.stop_mix_btn)

        self.music_volume = PercentSlider()
        self.music_volume.setValue(100)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(InfoDot("Общая громкость всей музыки в миксе относительно эмбиент-слоёв."))
        music_caption = QLabel("♪  Музыка — плейлист из шага 2")
        music_caption.setMinimumWidth(220)
        row.addWidget(music_caption)
        row.addWidget(self.music_volume, 1)
        console.body.addLayout(row)

        ambient_tip = (
            "Эмбиент-слой — фоновый звук (дождь, камин, волны): лупится бесшовно "
            "на весь ролик и мягко затихает в конце. Тумблер включает слой в микс, "
            "▶ — прослушать файл с громкостью ползунка."
        )
        self.ambient_rows = [AmbientRow(i + 1, info_tip=ambient_tip) for i in range(3)]
        for row_widget in self.ambient_rows:
            console.body.addWidget(row_widget)
            row_widget.play_requested.connect(self.ambient_play_requested)
            row_widget.builtin_requested.connect(self.ambient_builtin_requested)

        presets = Panel()
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addWidget(
            InfoDot(
                "Пресет запоминает громкость музыки и три эмбиент-слоя. "
                "Выбор пресета в списке сразу применяет его к форме."
            )
        )
        preset_row.addWidget(QLabel("Пресет микса"))
        self.mix_preset_combo = QComboBox()
        self.mix_preset_combo.setMinimumWidth(180)
        preset_row.addWidget(self.mix_preset_combo)
        self.save_preset_btn = QPushButton("Сохранить пресет")
        self.save_preset_btn.setToolTip("Запомнить текущий баланс микса как пресет")
        preset_row.addWidget(self.save_preset_btn)
        self.delete_preset_btn = QPushButton("Удалить")
        self.delete_preset_btn.setObjectName("TextDanger")
        self.delete_preset_btn.setToolTip("Удалить выбранный пресет (с подтверждением)")
        preset_row.addWidget(self.delete_preset_btn)
        preset_row.addStretch(1)
        presets.body.addLayout(preset_row)

        self.save_preset_btn.clicked.connect(self.save_mix_preset_clicked)
        self.delete_preset_btn.clicked.connect(self.delete_mix_preset_clicked)
        self.listen_mix_btn.clicked.connect(self.listen_mix_clicked)
        self.stop_mix_btn.clicked.connect(self.stop_mix_clicked)
        self.mix_preset_combo.textActivated.connect(self.mix_preset_selected)
        return self._page(console, presets)

    # --- шаг 4 · Эффекты --------------------------------------------------------

    def _build_page_effects(self) -> QWidget:
        motion = Panel("Луп и движение")
        self.smooth_start_toggle = Toggle()
        self.smooth_start_sec = LabeledSlider(1, 3, step=0.5, suffix=" с")
        self.smooth_start_sec.setValue(2.0)
        self.smooth_start_sec.setEnabled(False)
        self.smooth_start_toggle.toggled.connect(self.smooth_start_sec.setEnabled)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Первые 30 секунд решают удержание: видео появляется из чёрного, "
                "музыка и эмбиент вступают мягко (длительность — ползунок). При "
                "паузах между треками каждый следующий трек тоже вступает плавно."
            )
        )
        row.addWidget(self.smooth_start_toggle)
        row.addWidget(QLabel("Плавный старт из чёрного"))
        row.addWidget(self.smooth_start_sec, 1)
        motion.body.addLayout(row)

        self.seamless_toggle = Toggle()
        self.seamless_toggle.setChecked(True)
        self.loop_crossfade = LabeledSlider(0.5, 3, step=0.1, suffix=" с")
        self.loop_crossfade.setValue(1.0)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Конец видеофона плавно растворяется в его начале — зритель не "
                "видит «рестарта» картинки. Ползунок — длительность перехода. "
                "Проверить стык: кнопка «Проверить швы» (шаг 6)."
            )
        )
        row.addWidget(self.seamless_toggle)
        row.addWidget(QLabel("Бесшовный луп конец → начало"))
        row.addWidget(self.loop_crossfade, 1)
        motion.body.addLayout(row)

        self.ken_burns_toggle = Toggle()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "«Дыхание камеры»: очень медленный зум туда-обратно (~40-секундные "
                "циклы, до 8%). Оживляет статичную картинку; стык лупа остаётся "
                "бесшовным."
            )
        )
        row.addWidget(self.ken_burns_toggle)
        row.addWidget(QLabel("Кен Бёрнс — дыхание камеры"))
        row.addStretch(1)
        motion.body.addLayout(row)

        film = Panel("Плёнка и оверлеи")
        self.lut_combo = QComboBox()
        for label, _ in _LUT_ITEMS:
            self.lut_combo.addItem(label)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Готовый тон всего ролика: тёплый, холодный, ночной, VHS, сепия. "
                "Применяется при рендере — посмотреть можно «Предпросмотром 1 минута»."
            )
        )
        row.addWidget(QLabel("Цветовой фильтр"))
        row.addWidget(self.lut_combo)
        row.addStretch(1)
        film.body.addLayout(row)

        self.overlay_picker = FilePicker(
            "VFX-оверлей: дождь, снег, боке… (mp4 = screen, webm = альфа)",
            name_filter="Видео-оверлей (*.mp4 *.webm *.mov);;Все файлы (*)",
        )
        self.overlay_builtin_btn = QPushButton("Встроенные…")
        self.overlay_builtin_btn.setToolTip("Выбрать эффект из встроенной библиотеки LoopStudio")
        overlay_row = QHBoxLayout()
        overlay_row.setSpacing(8)
        overlay_row.addWidget(
            InfoDot(
                "Видео-эффект поверх кадра: дождь, снег, пыль, боке. mp4 "
                "накладывается режимом «screen» (тёмное становится прозрачным), "
                "webm — со своей прозрачностью. Файл лупится на весь ролик."
            )
        )
        overlay_row.addWidget(self.overlay_picker, 1)
        overlay_row.addWidget(self.overlay_builtin_btn)
        film.body.addLayout(overlay_row)
        self.overlay_builtin_btn.clicked.connect(self.overlay_builtin_clicked)

        self.overlay_opacity = PercentSlider()
        self.overlay_opacity.setValue(50)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(InfoDot("Сила оверлея: 0% — невидим, 100% — в полную силу."))
        row.addWidget(QLabel("Непрозрачность оверлея"))
        row.addWidget(self.overlay_opacity, 1)
        film.body.addLayout(row)

        self.vignette_toggle = Toggle()
        self.vignette_strength = PercentSlider()
        self.vignette_strength.setValue(30)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Мягкое затемнение краёв кадра — взгляд собирается к центру. "
                "Ползунок — сила затемнения."
            )
        )
        row.addWidget(self.vignette_toggle)
        row.addWidget(QLabel("Виньетка"))
        row.addWidget(self.vignette_strength, 1)
        film.body.addLayout(row)

        self.grain_toggle = Toggle()
        self.grain_strength = LabeledSlider(1, 30, step=1, suffix="")
        self.grain_strength.setValue(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Лёгкий плёночный шум: скрывает «пластиковость» и полосы на "
                "градиентах (небо, туман). Ползунок — сила зерна."
            )
        )
        row.addWidget(self.grain_toggle)
        row.addWidget(QLabel("Плёночное зерно"))
        row.addWidget(self.grain_strength, 1)
        film.body.addLayout(row)

        # колонки заканчиваются на одной линии (референс 04): последняя карточка
        # каждой колонки дорастает до общего низа, контент внутри прижат вверх
        motion.body.addStretch(1)
        film.body.addStretch(1)
        columns = QWidget()
        columns_row = QHBoxLayout(columns)
        columns_row.setContentsMargins(0, 0, 0, 0)
        columns_row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(motion, 1)
        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(film, 1)
        columns_row.addLayout(left, 1)
        columns_row.addLayout(right, 1)
        return self._page(columns)

    # --- шаг 5 · Экспорт --------------------------------------------------------

    def _build_page_export(self) -> QWidget:
        quality = Panel("Качество")
        self.resolution_group = QButtonGroup(self)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Размер кадра готового ролика. 1080p — стандарт YouTube; 4K "
                "заметно дольше рендерится и весит в разы больше."
            )
        )
        row.addWidget(self._labeled("Разрешение"))
        for i, (label, _) in enumerate(_RESOLUTIONS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            self.resolution_group.addButton(btn, i)
            row.addWidget(btn)
        row.addStretch(1)
        quality.body.addLayout(row)
        self.resolution_group.button(1).setChecked(True)  # 1080p

        self.quality_group = QButtonGroup(self)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(
            InfoDot(
                "Качество кодирования: «Как источник» — максимум качества и самый "
                "большой файл; «Высокое» — лучший баланс (рекомендуется); "
                "«Стандарт» — компактнее, для черновиков и longplay-эфиров."
            )
        )
        row.addWidget(self._labeled("Режим"))
        for i, (label, _) in enumerate(_QUALITIES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            self.quality_group.addButton(btn, i)
            row.addWidget(btn)
        row.addStretch(1)
        quality.body.addLayout(row)
        self.quality_group.button(1).setChecked(True)  # высокое

        duration = Panel()
        # служебный ползунок: контракт с контроллером и синхронизация чипов
        self.duration_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.duration_slider.setRange(1, 600)  # минуты
        self.duration_slider.setValue(60)
        self.duration_slider.hide()
        self.duration_edit = QTimeEdit()
        self.duration_edit.setDisplayFormat("HH:mm")
        self.duration_edit.setTime(QTime(1, 0))
        self.duration_edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.duration_edit.setFixedWidth(74)
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        duration_title = QLabel("Длительность")
        duration_title.setObjectName("PanelTitle")
        head_row.addWidget(duration_title)
        head_row.addWidget(
            InfoDot(
                "Целевая длительность ролика (до 10 часов): чипы — быстрый выбор, "
                "точное значение вписывается в поле часы:минуты. Итог может выйти "
                "чуть длиннее — последний трек всегда доигрывает целиком."
            )
        )
        head_row.addStretch(1)
        head_row.addWidget(self.duration_edit)  # значение — в правом верхнем углу
        duration.body.addLayout(head_row)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        self._duration_chips: list[QPushButton] = []
        for label, minutes in _DURATION_CHIPS:
            chip = QPushButton(label)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setToolTip(f"Установить длительность {label}")
            chip.clicked.connect(lambda _c=False, m=minutes: self.duration_slider.setValue(m))
            self._duration_chips.append(chip)
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        duration.body.addLayout(chips_row)
        hint = QLabel("Музыка не обрезается: последний трек доигрывает целиком.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        duration.body.addWidget(hint)

        folder = Panel("Папка и имя")
        self.output_picker = FilePicker("Куда сохранять готовые ролики", directory=True)
        if self._settings.last_output_dir:
            self.output_picker.setPath(self._settings.last_output_dir)
        folder.body.addLayout(
            self._info_row(
                "Куда складывать готовые ролики. Файлы никогда не перезаписываются — "
                "при совпадении имени добавляется « (2)».",
                self.output_picker,
            )
        )
        self.package_toggle = Toggle()
        self.package_toggle.setChecked(True)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.package_toggle)
        row.addWidget(QLabel("Каждый ролик — в отдельной папке"))
        row.addWidget(
            InfoDot(
                "Каждый ролик получает свою подпапку: mp4, трек-лист, кредиты, "
                "паспорт и копия проекта лежат вместе — ничего не перепутается "
                "в серийный день."
            )
        )
        row.addStretch(1)
        folder.body.addLayout(row)
        folder.body.addWidget(dotted_rule())
        self.title_edit = QLineEdit("LoopStudio {date}")
        folder.body.addLayout(
            self._info_row(
                "Имя файла и внутренний заголовок mp4. Подстановки заменяются при "
                "рендере: {date} — дата, {duration} — фактическая длительность, "
                "{folder} — имя папки музыки (удобно в пакетном режиме).",
                self.title_edit,
            )
        )
        tokens_row = QHBoxLayout()
        tokens_row.setSpacing(6)
        for token in ("{date}", "{duration}", "{folder}"):
            chip = QPushButton(token)
            chip.setObjectName("TokenChip")
            chip.setToolTip("Клик вставляет шаблон в имя")
            chip.clicked.connect(lambda _c=False, t=token: self.title_edit.insert(t))
            tokens_row.addWidget(chip)
        tokens_hint = QLabel("— клик вставляет шаблон")
        tokens_hint.setObjectName("Hint")
        tokens_row.addWidget(tokens_hint)
        tokens_row.addStretch(1)
        folder.body.addLayout(tokens_row)

        metadata = Panel("Метаданные")
        self.clean_metadata_toggle = Toggle()
        self.clean_metadata_toggle.setChecked(True)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.clean_metadata_toggle)
        row.addWidget(QLabel("Очистить метаданные (нейтральные: название, дата)"))
        row.addWidget(
            InfoDot(
                "Убирает из файла следы исходников (названия, программы, теги) и "
                "оставляет нейтральные: заголовок ролика и дату рендера. Чистый "
                "файл для загрузки; главы треков при этом сохраняются."
            )
        )
        row.addStretch(1)
        metadata.body.addLayout(row)

        self.duration_slider.valueChanged.connect(self._slider_to_time)
        self.duration_edit.timeChanged.connect(self._time_to_slider)
        self._sync_duration_chips()

        # колонки заканчиваются на одной линии (референс 05): последняя карточка
        # каждой колонки дорастает до общего низа, контент внутри прижат вверх
        duration.body.addStretch(1)
        metadata.body.addStretch(1)
        columns = QWidget()
        columns_row = QHBoxLayout(columns)
        columns_row.setContentsMargins(0, 0, 0, 0)
        columns_row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(quality)
        left.addWidget(duration, 1)
        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(folder)
        right.addWidget(metadata, 1)
        columns_row.addLayout(left, 1)
        columns_row.addLayout(right, 1)
        return self._page(columns)

    # --- шаг 6 · Генерация ------------------------------------------------------

    def _build_page_render(self) -> QWidget:
        actions = QWidget()
        buttons = QHBoxLayout(actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.generate_btn = QPushButton("Сгенерировать")
        self.generate_btn.setObjectName("Primary")
        self.generate_btn.setIcon(dot_icon())
        theme.apply_button_shadow(self.generate_btn)
        self.generate_btn.setToolTip(
            "Поставить ролик в очередь рендера (в пакетном режиме — все выбранные "
            "папки). Перед стартом пройдёт быстрая проверка: файлы, место на диске, "
            "конфликты настроек."
        )
        self.preview_btn = QPushButton("Предпросмотр 1 мин")
        self.preview_btn.setToolTip("Случайное место ролика — общее впечатление")
        self.preview_start_btn = QPushButton("Начало ролика")
        self.preview_start_btn.setToolTip(
            "Минутное превью строго с самого начала: интро, вступление первого "
            "трека, вход эмбиента — то, что решает удержание."
        )
        self.seams_btn = QPushButton("Проверить швы")
        self.seams_btn.setToolTip(
            "Короткая нарезка стыков: шов видео-лупа, интро → луп, луп → аутро. "
            "Пара минут вместо перемотки готового многочасового файла."
        )
        self.open_folder_btn = QPushButton("Открыть папку")
        self.open_folder_btn.setToolTip("Открыть папку сохранения (шаг 5) в проводнике")
        self.summary_btn = QPushButton("Итоги очереди")
        self.summary_btn.setToolTip(
            "Таблица всех заданий: статусы, время рендера, предупреждения. "
            "Двойной клик по готовому — открыть ролик."
        )
        buttons.addWidget(self.generate_btn)
        buttons.addWidget(self.preview_btn)
        buttons.addWidget(self.preview_start_btn)
        buttons.addWidget(self.seams_btn)
        buttons.addWidget(self.open_folder_btn)
        buttons.addWidget(self.summary_btn)
        buttons.addStretch(1)

        progress_panel = DarkPanel()
        self.progress_label = QLabel("Готов к работе")
        self.progress_label.setObjectName("Mono")
        self.progress_label.setWordWrap(True)
        self.open_log_btn = QPushButton("Журнал сбоя рендера")
        self.open_log_btn.setToolTip(
            "Подробности последнего сбоя рендера — файл рядом с роликами "
            "(не путать с общим журналом приложения в меню Проект)"
        )
        self.open_log_btn.hide()
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip(
            "Остановить выполняющийся рендер. Ждущие задания отменяются правым "
            "кликом по строке очереди."
        )
        rec_dot = QLabel("●")
        rec_dot.setStyleSheet(f"color: {theme.ACCENT}; background: transparent; font-size: 11px;")
        progress_panel.header_row.insertWidget(0, rec_dot)
        progress_panel.header_row.insertWidget(1, self.progress_label, 1)
        progress_panel.header_row.addWidget(self.open_log_btn)
        progress_panel.header_row.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        progress_panel.body.addWidget(self.progress_bar)

        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self.forecast_label = QLabel()
        self.forecast_label.setObjectName("Muted")
        self.forecast_label.setWordWrap(True)
        self.forecast_label.hide()  # прогноз пре-флайта: длительность и размер
        self.keep_awake_label = QLabel()
        self.keep_awake_label.setObjectName("Muted")
        self.keep_awake_label.setWordWrap(True)
        self.keep_awake_label.hide()  # «Сон Windows отключён…» — ставит контроллер
        info_row.addWidget(self.forecast_label, 1)
        info_row.addWidget(self.keep_awake_label, 1)
        progress_panel.body.addLayout(info_row)

        shutdown_panel = Panel()
        shutdown_row = QHBoxLayout()
        shutdown_row.setSpacing(8)
        self.shutdown_toggle = Toggle()
        shutdown_row.addWidget(self.shutdown_toggle)
        shutdown_row.addWidget(QLabel("Выключить компьютер после завершения очереди"))
        shutdown_row.addWidget(
            InfoDot(
                "Когда все задания очереди завершатся, компьютер выключится — "
                "с обратным отсчётом 60 секунд и кнопкой отмены. Пока очередь "
                "работает, сон Windows отключён автоматически."
            )
        )
        shutdown_row.addStretch(1)
        shutdown_panel.body.addLayout(shutdown_row)

        queue_panel = Panel()
        queue_head = QHBoxLayout()
        queue_title = QLabel("Очередь рендера")
        queue_title.setObjectName("PanelTitle")
        queue_hint = QLabel("— правый клик по заданию: действия")
        queue_hint.setObjectName("Hint")
        queue_head.addWidget(queue_title)
        queue_head.addWidget(queue_hint)
        queue_head.addStretch(1)
        queue_panel.body.addLayout(queue_head)
        self.queue_list = QListWidget()
        self.queue_list.setObjectName("QueueList")
        self.queue_list.setMinimumHeight(150)
        self.queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.setItemDelegate(QueueRowDelegate(self.queue_list))
        queue_panel.body.addWidget(self.queue_list)

        self.generate_btn.clicked.connect(self.generate_clicked)
        self.preview_btn.clicked.connect(self.preview_clicked)
        self.preview_start_btn.clicked.connect(self.preview_start_clicked)
        self.seams_btn.clicked.connect(self.seams_clicked)
        self.open_folder_btn.clicked.connect(self.open_folder_clicked)
        self.cancel_btn.clicked.connect(self.cancel_clicked)
        return self._page(actions, progress_panel, shutdown_panel, queue_panel)

    # --- синхронизация длительности ------------------------------------------

    def _slider_to_time(self, minutes: int) -> None:
        if self.duration_edit.time() != QTime(minutes // 60, minutes % 60):
            self.duration_edit.setTime(QTime(minutes // 60, minutes % 60))
        self._sync_duration_chips()

    def _time_to_slider(self, value: QTime) -> None:
        minutes = value.hour() * 60 + value.minute()
        if minutes >= 1 and minutes != self.duration_slider.value():
            self.duration_slider.setValue(minutes)
        self._sync_duration_chips()

    def _sync_duration_chips(self) -> None:
        minutes = self.duration_slider.value()
        for chip, (_label, m) in zip(self._duration_chips, _DURATION_CHIPS, strict=False):
            chip.setChecked(m == minutes)

    # --- треки ------------------------------------------------------------------

    def _add_track_item(self, name: str, *, checked: bool = True) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.track_list.addItem(item)
        return item

    def _reload_tracks(self, folder: str) -> None:
        self.track_list.clear()
        if folder:
            try:
                for track in scan_music_folder(Path(folder)):
                    self._add_track_item(track.name)
            except OSError:
                # папка без прав/отвалившийся диск: пустой список, не крэш;
                # понятную ошибку пользователь получит на пре-флайте
                pass
        self._refresh_track_counter()

    def track_order(self) -> list[str]:
        return [self.track_list.item(i).text() for i in range(self.track_list.count())]

    def excluded_tracks(self) -> list[str]:
        return [
            self.track_list.item(i).text()
            for i in range(self.track_list.count())
            if self.track_list.item(i).checkState() != Qt.CheckState.Checked
        ]

    def set_track_states(self, order: list[str], excluded: list[str]) -> None:
        """Перестроить список треков: порядок + галочки исключения."""
        existing = set(self.track_order())
        excluded_set = set(excluded)
        self.track_list.clear()
        for name in order:
            if name in existing:
                self._add_track_item(name, checked=name not in excluded_set)
        for name in sorted(existing - set(order)):
            self._add_track_item(name, checked=name not in excluded_set)
        self._refresh_track_counter()

    def set_batch_state(self, folder_count: int | None) -> None:
        """Показать/сбросить пакетный режим: N папок вместо одной.

        Рендер пакета запускается обычной кнопкой «Сгенерировать» (шаг 6) —
        выбор папок сам по себе ничего не стартует.
        """
        if folder_count:
            self.batch_btn.setText(f"Пакет: {folder_count} папок — изменить…")
            self.batch_clear_btn.show()
            self.music_picker.setEnabled(False)
            self.music_picker.setToolTip(
                "Включён пакетный режим — используются выбранные папки. "
                "Нажмите «×», чтобы вернуться к одной папке."
            )
        else:
            self.batch_btn.setText("Несколько папок…")
            self.batch_clear_btn.hide()
            self.music_picker.setEnabled(True)
            self.music_picker.setToolTip("")

    @staticmethod
    def _tracks_word(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            return "трек"
        if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            return "трека"
        return "треков"

    def _refresh_track_counter(self) -> None:
        total = self.track_list.count()
        active = total - len(self.excluded_tracks())
        if total == 0:
            self.track_counter.setText("Сторона A")
        elif active == total:
            self.track_counter.setText(f"Сторона A · {total} {self._tracks_word(total)}")
        else:
            self.track_counter.setText(f"Сторона A · активно {active} из {total}")

    def mark_new_tracks(self, names: set[str]) -> None:
        """Подсветить треки, которых не было в сохранённом проекте (добавлены в конец)."""
        highlight = QColor(theme.DESTRUCTIVE)
        for i in range(self.track_list.count()):
            item = self.track_list.item(i)
            if item.text() in names:
                item.setForeground(highlight)
                item.setToolTip("Новый трек: появился в папке после сохранения проекта")

    # --- биндинг RenderJob ------------------------------------------------------

    def collect_job(self) -> RenderJob:
        """Снять текущее состояние формы в RenderJob."""
        lut_key = _LUT_ITEMS[self.lut_combo.currentIndex()][1]
        resolution = _RESOLUTIONS[self.resolution_group.checkedId()][1]
        quality = _QUALITIES[self.quality_group.checkedId()][1]
        minutes = self.duration_slider.value()
        return RenderJob(
            video_path=self.video_picker.path(),
            intro_path=self.intro_picker.path(),
            outro_path=self.outro_picker.path(),
            source_audio=self.source_audio_toggle.isChecked(),
            source_audio_volume=self.source_audio_volume.fraction(),
            music_dir=self.music_picker.path(),
            track_order=self.track_order(),
            excluded_tracks=self.excluded_tracks(),
            fade_out=self.fade_out.value(),
            pause=self.pause.value(),
            crossfade=self.crossfade.value(),
            normalize=self.normalize_toggle.isChecked(),
            normalize_precise=self.normalize_precise_toggle.isChecked(),
            trim_silence=self.trim_silence_toggle.isChecked(),
            music_volume=self.music_volume.fraction(),
            ambient=[row.config() for row in self.ambient_rows],
            smooth_start=self.smooth_start_toggle.isChecked(),
            smooth_start_sec=self.smooth_start_sec.value(),
            effects=EffectsConfig(
                seamless_loop=self.seamless_toggle.isChecked(),
                loop_crossfade=self.loop_crossfade.value(),
                ken_burns=self.ken_burns_toggle.isChecked(),
                lut=lut_key,
                overlay_path=self.overlay_picker.path(),
                overlay_opacity=self.overlay_opacity.fraction(),
                overlay_screen_blend=not self.overlay_picker.path().lower().endswith(".webm"),
                vignette=self.vignette_toggle.isChecked(),
                vignette_strength=self.vignette_strength.fraction(),
                film_grain=self.grain_toggle.isChecked(),
                film_grain_strength=round(self.grain_strength.value()),
            ),
            resolution=resolution,  # type: ignore[arg-type]
            quality=quality,  # type: ignore[arg-type]
            duration_sec=minutes * 60.0,
            output_dir=self.output_picker.path(),
            package_folder=self.package_toggle.isChecked(),
            title_template=self.title_edit.text(),
            clean_metadata=self.clean_metadata_toggle.isChecked(),
        )

    def apply_job(self, job: RenderJob) -> None:
        """Показать проект в форме (открытие .lsproj)."""
        self.video_picker.setPath(job.video_path)
        self.intro_picker.setPath(job.intro_path)
        self.outro_picker.setPath(job.outro_path)
        self.source_audio_toggle.setChecked(job.source_audio)
        self.source_audio_volume.setFraction(job.source_audio_volume)
        self.music_picker.setPath(job.music_dir)
        if job.track_order or job.excluded_tracks:
            self.set_track_states(job.track_order or self.track_order(), job.excluded_tracks)
        self.fade_out.setValue(job.fade_out)
        self.pause.setValue(job.pause)
        self.crossfade.setValue(job.crossfade)
        self.normalize_toggle.setChecked(job.normalize)
        self.normalize_precise_toggle.setChecked(job.normalize_precise)
        self.trim_silence_toggle.setChecked(job.trim_silence)
        self.smooth_start_toggle.setChecked(job.smooth_start)
        self.smooth_start_sec.setValue(job.smooth_start_sec)
        self.music_volume.setFraction(job.music_volume)
        # проектов со слоями меньше трёх (старые/чужие) добиваем дефолтами:
        # иначе хвостовые строки формы сохранили бы состояние прошлого проекта
        layers = list(job.ambient) + [AmbientConfig()] * (len(self.ambient_rows) - len(job.ambient))
        for row, config in zip(self.ambient_rows, layers, strict=False):
            row.apply(config)
        fx = job.effects
        self.seamless_toggle.setChecked(fx.seamless_loop)
        self.loop_crossfade.setValue(fx.loop_crossfade)
        self.ken_burns_toggle.setChecked(fx.ken_burns)
        self.lut_combo.setCurrentIndex(
            next((i for i, (_, key) in enumerate(_LUT_ITEMS) if key == fx.lut), 0)
        )
        self.overlay_picker.setPath(fx.overlay_path)
        self.overlay_opacity.setFraction(fx.overlay_opacity)
        self.vignette_toggle.setChecked(fx.vignette)
        self.vignette_strength.setFraction(fx.vignette_strength)
        self.grain_toggle.setChecked(fx.film_grain)
        self.grain_strength.setValue(fx.film_grain_strength)
        self.resolution_group.button(
            next((i for i, (_, key) in enumerate(_RESOLUTIONS) if key == job.resolution), 1)
        ).setChecked(True)
        self.quality_group.button(
            next((i for i, (_, key) in enumerate(_QUALITIES) if key == job.quality), 1)
        ).setChecked(True)
        self.duration_slider.setValue(max(1, round(job.duration_sec / 60)))
        self.output_picker.setPath(job.output_dir)
        self.package_toggle.setChecked(job.package_folder)
        self.title_edit.setText(job.title_template)
        self.clean_metadata_toggle.setChecked(job.clean_metadata)
