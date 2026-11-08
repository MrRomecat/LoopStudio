"""Тема «Vinyl Warm» — светлая тёплая «аналоговая студия» (дизайн-хэндофф 2026-07).

Токены и QSS по спецификации LOOP-STUDIO/Дизайн/design_handoff_loopstudio:
фон #f6f0e4, панели #fffdf7, «чернила» #2b2620, жёсткие тени 3px 3px 0.
Красный #e33b2e — ТОЛЬКО микро-акценты (шпиндель, REC-точка, активный шаг,
звезда пресета, полоса тултипа). Деструктив — приглушённый #a3543f.

МАСШТАБ ИНТЕРФЕЙСА: build_stylesheet(scale) отдаёт QSS с размерами,
умноженными на масштаб (1.0 в обычном окне → до ~1.3 в развёрнутом), чтобы
широкий экран не выглядел пустым. Текущее значение хранится в UI_SCALE;
применяет масштаб core/ui/widgets.apply_ui_scale (стиль приложения + живые
scale-aware виджеты + ширина контента экрана).

Шрифты: Lora (заголовки/имена), Manrope (UI), JetBrains Mono (пути/числа).
TTF-файлы кладутся в assets/fonts и регистрируются load_fonts(); до этого
работают ближайшие системные за счёт фолбэков в семействах.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# --- палитра (дизайн-токены) --------------------------------------------------

BACKGROUND = "#f6f0e4"  # фон окна
CARD = "#fffdf7"  # светлые панели-карточки
CHIP = "#efe6d3"  # вторичный фон: чипы, кнопки, шапки списков
CHIP_HOVER = "#e7dcc4"
FIELD = "#fffdf7"  # поля ввода (на карточках)
FIELD_SELECTED = "#f6f0e4"  # заполненное поле файла
INK = "#2b2620"  # «чернила»: текст, тёмные панели, главные кнопки
INK_SOFT = "#3a342c"  # тёмные панели: ховеры/поля
INK_SOFT2 = "#453d32"
TEXT = INK
TEXT_SOFT = "#52483a"
TEXT_MUTED = "#6b5f4d"
TEXT_FAINT = "#a3947c"
TEXT_GHOST = "#c9b58f"
BORDER = "#e3d7bf"
BORDER_STRONG = "#cdbfa4"
SHADOW = "#e9dfc9"  # жёсткая тень панелей
SHADOW_BTN = "#cdbfa4"  # жёсткая тень кнопок
DOTTED = "#ece1cb"  # пунктирные разделители
ACCENT = "#e33b2e"  # YouTube-красный: только микро-детали
DESTRUCTIVE = "#a3543f"  # «Удалить», отмена, бейдж рендера
TOGGLE_ON = "#8a795d"
TOGGLE_OFF = "#ddd2ba"
TOGGLE_ON_DARK = "#55493a"  # тумблер на тёмной панели
TOGGLE_OFF_DARK = "#453d32"
LIGHT_ON_DARK = "#efe6d3"  # светлый текст на тёмном
LIGHT_ON_DARK_SOFT = "#d8cbb0"
LIGHT_ON_DARK_FAINT = "#a3947c"
# статусы очереди
STATUS_DONE = "#5c6b4d"
STATUS_DONE_BG = "#edf0e2"
STATUS_RENDER = "#a3543f"
STATUS_RENDER_BG = "#f8e8e2"
STATUS_CANCEL = "#a3947c"
STATUS_CANCEL_BG = "#efe6d3"

# устаревшее имя из тёмной темы: местами использовалось как «цвет ошибки»
ACCENT_HOVER = DESTRUCTIVE

# --- шрифтовые семейства (с фолбэками до установки TTF) -----------------------

SERIF = '"Lora", "Georgia", "Times New Roman", serif'
SANS = '"Manrope", "Segoe UI", sans-serif'
MONO = '"JetBrains Mono", "Cascadia Mono", "Consolas", monospace'

#: текущий масштаб интерфейса (меняет widgets.apply_ui_scale)
UI_SCALE = 1.0

#: ширина колонки контента при масштабе 1.0 (из макета)
CONTENT_WIDTH = 1120


def scaled(value: float) -> int:
    """Размер в px с учётом текущего масштаба интерфейса."""
    return max(1, round(value * UI_SCALE))


def load_fonts() -> None:
    """Зарегистрировать вшитые TTF из assets/fonts (вызывать после QApplication).

    Папки может не быть (шрифты ещё не скачаны) — тогда работают фолбэки.
    """
    from PySide6.QtGui import QFontDatabase

    from core.media.ffmpeg import app_root

    folder = app_root() / "assets" / "fonts"
    if not folder.is_dir():
        return
    loaded = 0
    for file in sorted(folder.glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(file)) != -1:
            loaded += 1
    log.info("Шрифты темы: загружено %d файлов из %s", loaded, folder)


def apply_button_shadow(widget: object) -> None:
    """Жёсткая тень 3px 3px 0 для кнопок (панели рисуют тень сами в paintEvent)."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

    assert isinstance(widget, QWidget)
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(0)
    effect.setOffset(3, 3)
    effect.setColor(QColor(SHADOW_BTN))
    widget.setGraphicsEffect(effect)


def build_stylesheet(scale: float = 1.0) -> str:
    """QSS темы для заданного масштаба интерфейса (1.0 — как в макете)."""

    def s(value: float) -> int:
        return max(1, round(value * scale))

    return f"""
QWidget {{
    background: {BACKGROUND};
    color: {TEXT};
    font-family: {SANS};
    font-size: {s(13)}px;
}}

/* --- панели-карточки ------------------------------------------------------ */
/* фон, рамку и жёсткую тень панель рисует сама в paintEvent (widgets.Panel):
   QGraphicsDropShadowEffect на дробных DPI терял тень у части карточек */
QFrame#Panel {{ background: transparent; border: none; }}
QFrame#Panel QWidget {{ background: transparent; }}
/* интерактивные элементы внутри панелей возвращают свой фон
   (правило прозрачности выше их обнуляет — как когда-то в StepCard) */
QFrame#Panel QLineEdit, QFrame#Panel QTimeEdit, QFrame#Panel QAbstractSpinBox,
QFrame#Panel QComboBox, QFrame#Panel QListWidget {{
    background: {FIELD};
}}
QFrame#Panel QPushButton {{ background: {CHIP}; }}
QFrame#Panel QPushButton:hover {{ background: {CHIP_HOVER}; }}
QFrame#Panel QPushButton:checked {{
    background: {INK};
    border-color: {INK};
    color: {LIGHT_ON_DARK};
}}
QFrame#Panel QPushButton#TextDanger {{ background: transparent; }}
/* тёмные кнопки внутри светлых панелей (PreviuStudio): правило панели выше
   перекрывало их фон чипом, а светлый текст оставался — текст пропадал */
QFrame#Panel QPushButton#Primary, QFrame#Panel QPushButton#PillDark {{
    background: {INK};
    border: none;
    color: {LIGHT_ON_DARK};
}}
/* пилюля — отдельным правилом: радиус обязан стоять РЯДОМ с border: none,
   иначе шорткат border в специфичном правиле Qt сбрасывает радиус из общего */
QFrame#Panel QPushButton#PrimaryPill {{
    background: {INK};
    border: none;
    border-radius: {s(16)}px;
    color: {LIGHT_ON_DARK};
}}
QFrame#Panel QPushButton#Primary:hover, QFrame#Panel QPushButton#PillDark:hover,
QFrame#Panel QPushButton#PrimaryPill:hover {{
    background: {INK_SOFT};
}}
QFrame#Panel QPushButton#Primary:disabled, QFrame#Panel QPushButton#PillDark:disabled,
QFrame#Panel QPushButton#PrimaryPill:disabled {{
    background: {BORDER_STRONG};
    color: {CARD};
}}
QFrame#Panel QPushButton#PillLight {{ background: {CARD}; }}
QFrame#Panel QPushButton#PillLight:hover {{ background: {CHIP_HOVER}; }}
QFrame#Panel QRadioButton {{ background: {CARD}; }}
QFrame#Panel QRadioButton:hover {{ background: {CHIP_HOVER}; }}
QFrame#Panel QRadioButton:checked {{
    background: {INK};
    border-color: {INK};
    color: {LIGHT_ON_DARK};
}}
/* текстовые переключатели PreviuStudio (шаг «Превью», v1.0.6; с v1.0.7 —
   QPushButton: у QRadioButton стиль Windows 11 рисовал огрызок индикатора
   поверх QSS-нуля): панельные правила выше специфичнее глобального
   #TextSwitch, поэтому фон и цвет гасятся и здесь, а не только ниже */
QFrame#Panel QPushButton#TextSwitch {{ background: transparent; color: {TEXT_MUTED}; }}
QFrame#Panel QPushButton#TextSwitch:hover {{ background: transparent; color: {INK}; }}
QFrame#Panel QPushButton#TextSwitch:checked {{
    background: transparent;
    color: {INK};
}}
QFrame#Panel QProgressBar {{ background: {INK_SOFT2}; }}
QLabel#PanelTitle {{
    font-family: {SERIF};
    font-size: {s(14)}px;
    font-weight: 600;
    background: transparent;
}}

/* тёмная панель (ПЕРЕХОДЫ, MIXING CONSOLE, прогресс) — фон рисует paintEvent */
QFrame#DarkPanel {{ background: transparent; border: none; color: {LIGHT_ON_DARK}; }}
QFrame#DarkPanel QWidget {{ background: transparent; color: {LIGHT_ON_DARK}; }}
QFrame#DarkPanel QLabel {{ color: {LIGHT_ON_DARK}; }}
QFrame#DarkPanel QLabel#Muted {{ color: {LIGHT_ON_DARK_FAINT}; }}
QLabel#DarkPanelTitle {{
    font-family: {MONO};
    font-size: {s(11)}px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: {LIGHT_ON_DARK};
}}
QFrame#DarkPanel QPushButton {{
    background: {LIGHT_ON_DARK};
    color: {INK};
    border: none;
}}
QFrame#DarkPanel QPushButton:hover {{ background: {LIGHT_ON_DARK_SOFT}; }}
QFrame#DarkPanel QSlider::groove:horizontal {{ background: {INK_SOFT2}; }}
QFrame#DarkPanel QSlider::sub-page:horizontal {{ background: {LIGHT_ON_DARK_SOFT}; }}
QFrame#DarkPanel QSlider::handle:horizontal {{ background: {LIGHT_ON_DARK_SOFT}; }}
QFrame#DarkPanel QSlider::sub-page:horizontal:disabled {{ background: {INK_SOFT2}; }}
QFrame#DarkPanel QSlider::handle:horizontal:disabled {{ background: {INK_SOFT}; }}
QFrame#DarkPanel QLineEdit, QFrame#DarkPanel QComboBox {{
    background: {INK_SOFT2};
    border: 1px solid {INK_SOFT2};
    color: {LIGHT_ON_DARK};
}}
QFrame#DarkPanel QLineEdit:disabled {{ color: {LIGHT_ON_DARK_FAINT}; }}
QFrame#DarkPanel QPushButton:disabled {{
    background: {INK_SOFT};
    color: {LIGHT_ON_DARK_FAINT};
}}
QFrame#DarkPanel QLabel#InfoDot {{ color: {LIGHT_ON_DARK_FAINT}; border-color: {INK_SOFT2}; }}
QFrame#DarkPanel QProgressBar {{ color: {INK}; }}

/* --- шапка ---------------------------------------------------------------- */
QFrame#AppHeader {{ background: {BACKGROUND}; border: none; }}
QLabel#AppTitle {{
    font-family: {SERIF};
    font-size: {s(17)}px;
    font-weight: 600;
    background: transparent;
}}
QLabel#AppVersion {{
    font-family: {MONO};
    font-size: {s(10)}px;
    color: {TEXT_FAINT};
    background: transparent;
}}
QFrame#HeaderRule {{ background: {INK}; border: none; min-height: 2px; max-height: 2px; }}
QFrame#ProfileChip {{
    background: {CHIP};
    border: none;
    border-radius: {s(15)}px;
}}
QFrame#ProfileChip QWidget {{ background: transparent; }}
QFrame#ProfileChip QComboBox {{
    background: transparent;
    border: none;
    padding: {s(2)}px {s(4)}px;
    font-weight: 600;
}}
QPushButton#PillLight {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: {s(12)}px;
    padding: {s(4)}px {s(14)}px;
}}
QPushButton#PillLight:hover {{ background: {CHIP_HOVER}; }}
QPushButton#TextDanger {{
    background: transparent;
    border: none;
    color: {DESTRUCTIVE};
    padding: {s(4)}px {s(8)}px;
}}
QPushButton#TextDanger:hover {{ color: {ACCENT}; }}
QPushButton#PillDark {{
    background: {INK};
    color: {LIGHT_ON_DARK};
    border: none;
    border-radius: {s(15)}px;
    padding: {s(6)}px {s(16)}px;
    font-weight: 700;
}}
QPushButton#PillDark:hover {{ background: {INK_SOFT}; }}

/* --- степпер и заголовки шага --------------------------------------------- */
QLabel#StepTitle {{
    font-family: {SERIF};
    font-size: {s(26)}px;
    font-weight: 600;
    background: transparent;
}}
QLabel#StepSubtitle {{
    font-family: {SERIF};
    font-style: italic;
    font-size: {s(13)}px;
    color: {TEXT_FAINT};
    background: transparent;
}}
QLabel#StepCounter {{
    font-family: {SERIF};
    font-style: italic;
    font-size: {s(12)}px;
    color: {TEXT_FAINT};
    background: transparent;
}}

/* --- текстовые роли -------------------------------------------------------- */
QLabel {{ background: transparent; }}
QLabel#Muted {{ color: {TEXT_FAINT}; background: transparent; }}
QLabel#Hint {{
    font-family: {SERIF};
    font-style: italic;
    font-size: {s(12)}px;
    color: {TEXT_FAINT};
    background: transparent;
}}
QLabel#Mono {{ font-family: {MONO}; background: transparent; }}
QLabel#InfoDot {{
    color: {TEXT_GHOST};
    background: transparent;
    border: 1px solid {BORDER_STRONG};
    border-radius: {s(7)}px;
    font-family: {MONO};
    font-size: {s(9)}px;
    font-weight: 600;
}}
QLabel#InfoDot:hover {{ color: {TEXT}; border-color: {ACCENT}; }}

/* --- поля ввода ------------------------------------------------------------ */
QLineEdit, QTimeEdit, QAbstractSpinBox, QPlainTextEdit, QComboBox, QListWidget {{
    background: {FIELD};
    border: 1px solid {BORDER};
    border-radius: {s(4)}px;
    padding: {s(6)}px {s(8)}px;
    selection-background-color: {INK};
    selection-color: {LIGHT_ON_DARK};
}}
QLineEdit, QTimeEdit, QAbstractSpinBox {{ font-family: {MONO}; font-size: {s(12)}px; }}
/* моноширинный блок результата (промты, коды): MuseStudio и будущие модули */
QPlainTextEdit#MonoBlock {{ font-family: {MONO}; font-size: {s(12)}px; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_GHOST}; }}
QComboBox {{ font-family: {SANS}; }}
QComboBox::drop-down {{ border: none; width: {s(22)}px; }}
/* стрелка комбо-бокса: треугольник рамками (картинок в теме нет) */
QComboBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: {s(4)}px solid transparent;
    border-right: {s(4)}px solid transparent;
    border-top: {s(5)}px solid {TEXT_MUTED};
    margin-right: {s(6)}px;
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {INK};
    selection-color: {LIGHT_ON_DARK};
}}

/* --- кнопки ---------------------------------------------------------------- */
QPushButton {{
    background: {CHIP};
    border: 1px solid {BORDER_STRONG};
    border-radius: {s(4)}px;
    padding: {s(6)}px {s(14)}px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {CHIP_HOVER}; }}
QPushButton:pressed {{ background: {BORDER_STRONG}; }}
QPushButton:checked {{
    background: {INK};
    border-color: {INK};
    color: {LIGHT_ON_DARK};
}}
QPushButton:disabled {{ color: {TEXT_GHOST}; background: {CHIP}; }}
QPushButton#Primary {{
    background: {INK};
    border: none;
    font-weight: 700;
    color: {LIGHT_ON_DARK};
    padding: {s(8)}px {s(18)}px;
}}
QPushButton#Primary:hover {{ background: {INK_SOFT}; }}
QPushButton#Primary:disabled {{ background: {BORDER_STRONG}; color: {CARD}; }}
/* тёмная пилюля: главные кнопки ВНУТРИ шагов PreviuStudio (правка владельца
   v1.0.2: прямоугольным остаётся только «Продолжить» внизу экрана) */
QPushButton#PrimaryPill {{
    background: {INK};
    border: none;
    font-weight: 700;
    color: {LIGHT_ON_DARK};
    padding: {s(9)}px {s(20)}px;
    /* радиус не больше половины высоты: больший Qt отбрасывает целиком */
    border-radius: {s(16)}px;
}}
QPushButton#PrimaryPill:hover {{ background: {INK_SOFT}; }}
QPushButton#PrimaryPill:disabled {{ background: {BORDER_STRONG}; color: {CARD}; }}
/* текстовые переключатели (PreviuStudio, шаг «Превью») — «просто надписи»:
   активная — «чернилами» с подчёркиванием (нижняя граница 2px: рисуется
   надёжно, в отличие от text-decoration), остальные приглушены; граница
   зарезервирована и у неактивных, чтобы надписи не прыгали при выборе */
QPushButton#TextSwitch {{
    background: transparent;
    border: none;
    border-bottom: {s(2)}px solid transparent;
    border-radius: 0;
    padding: {s(2)}px 0;
    color: {TEXT_MUTED};
}}
QPushButton#TextSwitch:hover {{ background: transparent; color: {INK}; }}
QPushButton#TextSwitch:checked {{
    background: transparent;
    border-bottom: {s(2)}px solid {INK};
    color: {INK};
}}
QPushButton#Chip {{
    border-radius: {s(14)}px;
    padding: {s(5)}px {s(13)}px;
}}
QPushButton#TokenChip {{
    font-family: {MONO};
    font-size: {s(11)}px;
    border-radius: {s(4)}px;
    padding: {s(3)}px {s(8)}px;
    background: {CHIP};
}}
QPushButton#GhostBack {{
    background: transparent;
    border: none;
    font-weight: 600;
    padding: {s(6)}px {s(4)}px;
    text-align: left;
}}
QPushButton#GhostBack:hover {{ color: {TEXT_MUTED}; }}

/* сегмент-кнопки (разрешение/режим) — из QRadioButton */
QRadioButton {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: {s(4)}px;
    padding: {s(5)}px {s(12)}px;
    font-weight: 600;
}}
QRadioButton::indicator {{ width: 0; height: 0; }}
QRadioButton:hover {{ background: {CHIP_HOVER}; }}
QRadioButton:checked {{
    background: {INK};
    border-color: {INK};
    color: {LIGHT_ON_DARK};
}}

/* заглушка варианта превью (PreviuStudio, v1.0.7): держит место в сетке,
   пока картинка не сгенерирована — блок экспорта не прыгает по вертикали */
QFrame#ThumbGhost {{
    background: transparent;
    border: 1px dashed {BORDER_STRONG};
    border-radius: {s(6)}px;
}}

/* --- слайдеры --------------------------------------------------------------- */
QSlider {{ background: transparent; min-height: {s(20)}px; }}
QSlider::groove:horizontal {{
    height: {s(4)}px;
    background: {TOGGLE_OFF};
    border-radius: {s(2)}px;
}}
QSlider::sub-page:horizontal {{
    background: {TOGGLE_ON};
    border-radius: {s(2)}px;
}}
QSlider::handle:horizontal {{
    width: {s(8)}px;
    height: {s(16)}px;
    margin: -{s(6)}px 0;
    border-radius: {s(2)}px;
    background: {INK};
}}
QSlider::handle:horizontal:hover {{ background: {INK_SOFT}; }}
QSlider::sub-page:horizontal:disabled {{ background: {DOTTED}; }}
QSlider::handle:horizontal:disabled {{ background: {BORDER_STRONG}; }}

/* --- прогресс --------------------------------------------------------------- */
QProgressBar {{
    background: {INK_SOFT2};
    border: none;
    border-radius: {s(4)}px;
    text-align: center;
    height: {s(16)}px;
    color: {INK};
    font-family: {MONO};
    font-size: {s(10)}px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {LIGHT_ON_DARK_SOFT}, stop:1 {TEXT_GHOST});
    border-radius: {s(4)}px;
}}

/* --- списки ----------------------------------------------------------------- */
QListWidget {{ font-family: {SERIF}; font-size: {s(13)}px; }}
QListWidget::item {{ padding: {s(6)}px {s(6)}px; border-bottom: 1px dotted {DOTTED}; }}
/* круглая галочка включения трека — как отметка на конверте пластинки */
QListView::indicator {{
    width: {s(15)}px; height: {s(15)}px;
    border-radius: {s(8)}px;
    border: 2px solid {BORDER_STRONG};
    background: {CARD};
}}
QListView::indicator:checked {{
    border-color: {INK};
    background: {INK};
}}
QListWidget::item:selected {{
    background: {FIELD_SELECTED};
    color: {TEXT};
    border-radius: {s(3)}px;
}}

/* --- меню, скроллбары, тултипы ---------------------------------------------- */
QMenuBar {{ background: {BACKGROUND}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: {s(4)}px {s(10)}px; }}
QMenuBar::item:selected {{ background: {CHIP}; border-radius: {s(4)}px; }}
QMenu {{
    background: {INK};
    color: {LIGHT_ON_DARK};
    border: 1px solid {INK};
    border-radius: {s(5)}px;
    padding: {s(4)}px;
}}
QMenu::item {{ padding: {s(6)}px {s(14)}px; border-radius: {s(3)}px; }}
QMenu::item:selected {{ background: {DESTRUCTIVE}; color: #ffffff; }}
QScrollBar:vertical {{ background: {BACKGROUND}; width: {s(10)}px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: {s(5)}px;
    min-height: {s(30)}px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_GHOST}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {BACKGROUND}; height: {s(10)}px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: {s(5)}px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QToolTip {{
    background: {CARD};
    color: {TEXT_SOFT};
    border: 1px solid {BORDER};
    border-left: 3px solid {ACCENT};
    padding: {s(6)}px {s(9)}px;
    font-size: {s(12)}px;
}}

/* --- диалоги ---------------------------------------------------------------- */
QDialog {{ background: {BACKGROUND}; }}
QMessageBox {{ background: {BACKGROUND}; }}
QFrame#DialogRule {{ background: {INK}; border: none; min-height: 2px; max-height: 2px; }}
QLabel#DialogTitle {{
    font-family: {SERIF};
    font-size: {s(15)}px;
    font-weight: 600;
    background: transparent;
}}

/* --- очередь: строки собираются делегатом, фон списку --------------------- */
QListWidget#QueueList {{ font-family: {SANS}; }}
"""


STYLESHEET = build_stylesheet(1.0)
