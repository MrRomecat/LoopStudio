"""Общие виджеты Studio Suite: панели, степпер, слайдеры, Toggle, FilePicker.

Визуальный язык — тема «Vinyl Warm» (core/ui/theme.py): светлые карточки
с жёсткой тенью 3px 3px 0, тёмные панели-консоли, диски-«пластинки».
"""

from __future__ import annotations

import contextlib
import weakref
from collections.abc import Sequence

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.ui import theme

#: живые виджеты, умеющие перестраивать свои размеры под масштаб интерфейса
_scale_aware: weakref.WeakSet[QWidget] = weakref.WeakSet()


def _register_scale_aware(widget: QWidget) -> None:
    _scale_aware.add(widget)


def apply_ui_scale(scale: float) -> None:
    """Применить масштаб интерфейса: QSS приложения + живые виджеты.

    Вызывается окном при изменении ширины: широкое/развёрнутое окно получает
    крупнее шрифты, кнопки и элементы (до ~1.3), чтобы не выглядеть пустым.
    """
    from PySide6.QtWidgets import QApplication

    theme.UI_SCALE = scale
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyleSheet(theme.build_stylesheet(scale))
    for widget in list(_scale_aware):
        with contextlib.suppress(RuntimeError):  # C++-объект мог быть удалён
            widget.apply_scale(scale)  # type: ignore[attr-defined]


class InfoDot(QLabel):
    """Значок «i» в кружочке: наведи — узнаешь, за что отвечает поле.

    Стандарт проекта: у каждой функции (поле, тумблер, ползунок) впереди
    стоит такой значок с человеческим объяснением. Новые функции обязаны
    добавляться вместе со своим InfoDot.

    Подсказка показывается МГНОВЕННО по входу курсора в кружок (штатный
    QToolTip ждёт ~секунду и ощущается сломанным); активная область — весь
    виджет целиком.
    """

    def __init__(self, tip: str, parent: QWidget | None = None) -> None:
        super().__init__("i", parent)
        self.setObjectName("InfoDot")
        self.apply_scale(theme.UI_SCALE)
        _register_scale_aware(self)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        # <qt> включает перенос строк в подсказке — длинные тексты не уезжают за экран
        self._tip = f"<qt>{tip}</qt>"
        self.setToolTip(self._tip)  # запасной путь (клавиатура, скринридеры)

    def apply_scale(self, scale: float) -> None:
        size = max(1, round(15 * scale))
        self.setFixedSize(size, size)

    def enterEvent(self, event: QEnterEvent) -> None:
        """Показать подсказку сразу, без задержки Qt (Qt override)."""
        below_dot = self.mapToGlobal(QPoint(0, self.height() + 2))
        QToolTip.showText(below_dot, self._tip, self)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Убрать подсказку при уходе курсора (Qt override)."""
        QToolTip.hideText()
        super().leaveEvent(event)


def _paint_card(widget: QWidget, fill: str, border: str, shadow_px: int, radius: float) -> None:
    """Нарисовать карточку с жёсткой тенью 3px 3px 0 вручную.

    QGraphicsDropShadowEffect на дробных DPI (дисплей 125–175%) у части
    панелей молча не рисуется — тень фирменного стиля пропадала. Поэтому
    панель резервирует полосу справа/снизу и рисует смещённый
    прямоугольник-тень и карточку с рамкой сама: работает при любом DPI.
    """
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QRectF(0, 0, widget.width() - shadow_px, widget.height() - shadow_px)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.SHADOW))
    painter.drawRoundedRect(card.translated(shadow_px, shadow_px), radius, radius)
    painter.setBrush(QColor(fill))
    painter.setPen(QPen(QColor(border), 1))
    painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.end()


class Panel(QFrame):
    """Светлая карточка-панель с жёсткой тенью и необязательным заголовком."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        outer = QVBoxLayout(self)
        if title:
            caption = QLabel(title)
            caption.setObjectName("PanelTitle")
            outer.addWidget(caption)
        self.body = outer
        self.apply_scale(theme.UI_SCALE)
        _register_scale_aware(self)

    def apply_scale(self, scale: float) -> None:
        self._shadow_px = max(2, round(3 * scale))
        self._radius = 6 * scale
        margin = round(16 * scale)
        self.body.setContentsMargins(
            margin, round(14 * scale), margin + self._shadow_px, round(14 * scale) + self._shadow_px
        )
        self.body.setSpacing(round(9 * scale))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Карточка и тень рисуются вручную (Qt override), QSS-фона у панели нет."""
        _paint_card(self, theme.CARD, theme.BORDER, self._shadow_px, self._radius)

    def add_row(self, *widgets: QWidget, stretch_last: bool = True) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        for w in widgets:
            row.addWidget(w)
        if stretch_last:
            row.addStretch(1)
        self.body.addLayout(row)
        return row


class DarkPanel(QFrame):
    """Тёмная панель-«консоль» (ПЕРЕХОДЫ, MIXING CONSOLE, прогресс рендера).

    Заголовок — моно с красной точкой, как на пульте студийной записи.
    """

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DarkPanel")
        outer = QVBoxLayout(self)
        self.header_row = QHBoxLayout()
        self.header_row.setSpacing(8)
        if title:
            caption = QLabel(
                f'<span style="color:{theme.ACCENT}">●</span>&nbsp;&nbsp;{title.upper()}'
            )
            caption.setObjectName("DarkPanelTitle")
            self.header_row.addWidget(caption)
        self.header_row.addStretch(1)
        outer.addLayout(self.header_row)
        self.body = outer
        self.apply_scale(theme.UI_SCALE)
        _register_scale_aware(self)

    def apply_scale(self, scale: float) -> None:
        self._shadow_px = max(2, round(3 * scale))
        self._radius = 6 * scale
        margin = round(16 * scale)
        self.body.setContentsMargins(
            margin, round(13 * scale), margin + self._shadow_px, round(14 * scale) + self._shadow_px
        )
        self.body.setSpacing(round(9 * scale))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Тёмная карточка и тень рисуются вручную (Qt override)."""
        _paint_card(self, theme.INK, theme.INK, self._shadow_px, self._radius)


def dotted_rule(parent: QWidget | None = None) -> QFrame:
    """Пунктирный разделитель внутри карточек (фирменный приём макета)."""
    rule = QFrame(parent)
    rule.setObjectName("DottedRule")
    rule.setFixedHeight(2)
    rule.setStyleSheet(f"border: none; border-top: 2px dotted {theme.DOTTED}; background: none;")
    return rule


def dot_icon(color: str = theme.ACCENT, diameter: int = 8) -> QIcon:
    """Круглая точка-иконка (красная точка главной кнопки «Сгенерировать»)."""
    pixmap = QPixmap(diameter, diameter)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, diameter, diameter)
    painter.end()
    return QIcon(pixmap)


class VinylDisc(QWidget):
    """Пластинка: тёмный диск с красным «шпинделем» (логотип и мини-версии)."""

    def __init__(
        self, diameter: int = 26, parent: QWidget | None = None, *, spindle: str = theme.ACCENT
    ) -> None:
        super().__init__(parent)
        self._spindle = spindle
        self._base_diameter = diameter
        self.apply_scale(theme.UI_SCALE)
        _register_scale_aware(self)

    def apply_scale(self, scale: float) -> None:
        size = max(8, round(self._base_diameter * scale))
        self.setFixedSize(size, size)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        painter.setBrush(QColor(theme.INK))
        painter.drawEllipse(rect)
        # «дорожки» пластинки — едва заметное кольцо
        painter.setPen(QPen(QColor(theme.INK_SOFT2), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect.adjusted(4, 4, -4, -4))
        painter.setPen(Qt.PenStyle.NoPen)
        spindle = max(4.0, rect.width() * 0.24)
        painter.setBrush(QColor(self._spindle))
        painter.drawEllipse(rect.center(), spindle / 2, spindle / 2)
        painter.end()


class Stepper(QWidget):
    """Степпер мастера: шесть «пластинок» с подписями и соединителями.

    Пройденный шаг — залитый тёмный диск со светлым отверстием; активный —
    диск с «дорожками» и красной обводкой; будущий — светлый контур.
    Клик по шагу — свободный переход (это настройки, а не строгий визард).
    """

    step_clicked = Signal(int)  # 0-based индекс

    # крупные «пластинки» (+30% к макету) — акцент на музыкальность приложения
    _BASE_DISC = 44
    _BASE_GAP_TOP = 5
    _BASE_LABEL_H = 23

    def __init__(self, labels: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels = list(labels)
        self._current = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_scale(theme.UI_SCALE)
        _register_scale_aware(self)

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        self._DISC = round(self._BASE_DISC * scale)
        self._GAP_TOP = round(self._BASE_GAP_TOP * scale)
        self._LABEL_H = round(self._BASE_LABEL_H * scale)
        self.setFixedHeight(self._DISC + self._GAP_TOP + self._LABEL_H + round(6 * scale))
        self.setMinimumWidth(round(110 * scale) * len(self._labels))
        self.update()

    def current(self) -> int:
        return self._current

    def set_current(self, index: int) -> None:
        index = max(0, min(len(self._labels) - 1, index))
        if index != self._current:
            self._current = index
            self.update()

    # геометрия: центры дисков распределены равномерно по ширине
    def _centers(self) -> list[float]:
        count = len(self._labels)
        slot = self.width() / count
        return [slot * i + slot / 2 for i in range(count)]

    def mousePressEvent(self, event: QMouseEvent) -> None:
        centers = self._centers()
        x = event.position().x()
        nearest = min(range(len(centers)), key=lambda i: abs(centers[i] - x))
        self.step_clicked.emit(nearest)
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self._scale
        centers = self._centers()
        radius = self._DISC / 2
        cy = self._GAP_TOP + radius
        gap = round(10 * scale)

        # соединительные линии между дисками
        for i in range(len(centers) - 1):
            passed = i < self._current
            pen = QPen(QColor(theme.INK if passed else theme.BORDER_STRONG), 2)
            painter.setPen(pen)
            painter.drawLine(
                QPoint(round(centers[i] + radius + gap), round(cy)),
                QPoint(round(centers[i + 1] - radius - gap), round(cy)),
            )

        for i, cx in enumerate(centers):
            rect = QRectF(cx - radius, cy - radius, self._DISC, self._DISC)
            if i < self._current:  # пройденный: тёмный диск, светлое отверстие
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.INK))
                painter.drawEllipse(rect)
                painter.setBrush(QColor(theme.LIGHT_ON_DARK))
                painter.drawEllipse(rect.center(), 6 * scale, 6 * scale)
            elif i == self._current:  # активный: «дорожки» + красная обводка
                painter.setPen(QPen(QColor(theme.ACCENT), 2))
                painter.setBrush(QColor(theme.INK))
                painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
                painter.setPen(QPen(QColor(theme.INK_SOFT2), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                ring1 = 8 * scale
                ring2 = 13 * scale
                painter.drawEllipse(rect.adjusted(ring1, ring1, -ring1, -ring1))
                painter.drawEllipse(rect.adjusted(ring2, ring2, -ring2, -ring2))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.LIGHT_ON_DARK))
                painter.drawEllipse(rect.center(), 4.5 * scale, 4.5 * scale)
            else:  # будущий: светлый контур
                painter.setPen(QPen(QColor(theme.BORDER_STRONG), 2))
                painter.setBrush(QColor(theme.CHIP))
                painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.BORDER_STRONG))
                painter.drawEllipse(rect.center(), 3.5 * scale, 3.5 * scale)

            color = theme.TEXT if i == self._current else theme.TEXT_FAINT
            painter.setPen(QPen(QColor(color)))
            font = painter.font()
            font.setPointSizeF(11 * scale)
            font.setBold(i == self._current)
            painter.setFont(font)
            half_label = 62 * scale
            label_rect = QRectF(
                cx - half_label, cy + radius + 4 * scale, half_label * 2, self._LABEL_H
            )
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter, self._labels[i])
        painter.end()


class LabeledSlider(QWidget):
    """Слайдер со значением справа (моно, как на пульте: «55 %», «2 с»)."""

    valueChanged = Signal(float)

    def __init__(
        self,
        minimum: float,
        maximum: float,
        *,
        step: float = 1.0,
        suffix: str = " %",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._step = step
        self._suffix = suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(round(minimum / step), round(maximum / step))
        self._label = QLabel()
        self._label.setObjectName("Mono")
        self._label.setMinimumWidth(52)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._label)
        self._slider.valueChanged.connect(self._on_change)
        self._refresh()

    def _on_change(self) -> None:
        self._refresh()
        self.valueChanged.emit(self.value())

    def _refresh(self) -> None:
        self._label.setText(f"{self.value():g}{self._suffix}")

    def value(self) -> float:
        return self._slider.value() * self._step

    def setValue(self, value: float) -> None:
        self._slider.setValue(round(value / self._step))


class PercentSlider(LabeledSlider):
    """Громкость 0–100 % (значение наружу — доля 0..1)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 100, step=1.0, suffix=" %", parent=parent)

    def fraction(self) -> float:
        return self.value() / 100.0

    def setFraction(self, fraction: float) -> None:
        self.setValue(round(fraction * 100))


class Toggle(QAbstractButton):
    """Тумблер 28×16 в духе аналоговой студии (вкл — тёплый «латунный»)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Tab-обход не должен перепрыгивать тумблеры (Space переключает)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self.apply_scale(theme.UI_SCALE)
        _register_scale_aware(self)

    def apply_scale(self, scale: float) -> None:
        self.setFixedSize(round(28 * scale), round(16 * scale))

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QColor(theme.TOGGLE_ON) if self.isChecked() else QColor(theme.TOGGLE_OFF)
        if not self.isEnabled():
            track = QColor(theme.DOTTED)
        height = self.height()
        radius = height / 2
        painter.setBrush(track)
        painter.drawRoundedRect(self.rect(), radius, radius)
        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.ACCENT), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(1, 1, -1, -1), radius - 1, radius - 1
            )
            painter.setPen(Qt.PenStyle.NoPen)
        # белая ручка с одинаковыми полями со всех сторон (масштаб любой)
        margin = max(2.0, height * 0.125)
        handle = height - 2 * margin
        x = margin + self._offset * (self.width() - handle - 2 * margin)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(x, margin, handle, handle))
        painter.end()


class FilePicker(QWidget):
    """Поле пути + кнопка выбора файла или папки."""

    pathChanged = Signal(str)

    def __init__(
        self,
        placeholder: str,
        *,
        directory: bool = False,
        name_filter: str = "Все файлы (*)",
        button_text: str = "Выбрать…",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        self._filter = name_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._edit = QLineEdit()
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText(placeholder)
        self._button = QPushButton(button_text)
        self._button.setToolTip("Выбрать папку" if directory else "Выбрать файл")
        # «×» (знак умножения) есть в любом шрифте — крестик виден, а не пустой квадрат
        self._clear = QPushButton("×")
        self._clear.setFixedWidth(30)
        self._clear.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px;")
        self._clear.setToolTip("Убрать выбранный файл")
        self._clear.hide()
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._button)
        layout.addWidget(self._clear)
        self._button.clicked.connect(self._pick)
        self._clear.clicked.connect(lambda: self.setPath(""))

    def _pick(self) -> None:
        if self._directory:
            path = QFileDialog.getExistingDirectory(self, "Выбор папки", self.path())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Выбор файла", "", self._filter)
        if path:
            self.setPath(path)

    def path(self) -> str:
        return self._edit.text()

    def setPath(self, path: str) -> None:
        if path == self.path():
            return
        self._edit.setText(path)
        self._clear.setVisible(bool(path))
        self.pathChanged.emit(path)
