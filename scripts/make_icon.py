"""Генерация иконки приложения: пластинка «Vinyl Warm» → assets/icon.ico.

Рисунок повторяет логотип шапки (widgets.VinylDisc): тёмный диск с
«дорожками» и красным шпинделем на тёплой кремовой плашке — фирменный
стиль дизайн-хэндоффа. Старый красный луп-символ на чёрном (тёмная тема
до v1.5.0) больше не используется.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.media.ffmpeg import find_ffmpeg
from core.ui import theme

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    # тёплая кремовая плашка со скруглёнными углами
    corner = size * 0.14
    painter.setBrush(QColor(theme.BACKGROUND))
    painter.drawRoundedRect(QRectF(0, 0, size, size), corner, corner)

    # диск-пластинка
    margin = size * 0.11
    disc = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.setBrush(QColor(theme.INK))
    painter.drawEllipse(disc)

    # «дорожки» — два тонких кольца между шпинделем и краем диска
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(theme.INK_SOFT2), max(1.0, size * 0.01)))
    for factor in (0.62, 0.80):
        inset = disc.width() * (1 - factor) / 2
        painter.drawEllipse(disc.adjusted(inset, inset, -inset, -inset))

    # красный шпиндель
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.ACCENT))
    spindle = disc.width() * 0.30
    painter.drawEllipse(disc.center(), spindle / 2, spindle / 2)
    painter.end()
    return image


def main() -> int:
    QGuiApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)
    png = ASSETS / "icon_256.png"
    draw(256).save(str(png))
    ico = ASSETS / "icon.ico"
    subprocess.run(
        [str(find_ffmpeg()), "-y", "-loglevel", "error", "-i", str(png), str(ico)],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    print(f"Иконка готова: {ico}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
