"""Окно активации: без действительного ключа основной экран не открывается."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from core.licensing.storage import load_valid_license, save_activation
from core.licensing.validate import (
    PRODUCT_LOOP,
    PRODUCT_TITLES,
    LicenseError,
    LicenseInfo,
    validate_key,
)
from core.ui import theme


class ActivationDialog(QDialog):
    """Ввод лицензионного ключа при первом запуске.

    ``product`` — бит запускаемого модуля линейки: ключ должен включать его,
    иначе покажем, каких модулей ключу не хватает.
    """

    def __init__(self, parent: QWidget | None = None, *, product: int = PRODUCT_LOOP) -> None:
        super().__init__(parent)
        self._product = product
        product_title = PRODUCT_TITLES.get(product, "Studio Suite")
        self.setWindowTitle(f"Активация {product_title}")
        self.setModal(True)
        self.resize(520, 300)
        self.license_info: LicenseInfo | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        title = QLabel(f"Добро пожаловать в {product_title}")
        title.setObjectName("StepTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Вставьте лицензионный ключ из письма о покупке (начинается с LSTD-…). "
            "Активация привязывает ключ к этому компьютеру и работает оффлайн."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        self.key_edit = QPlainTextEdit()
        self.key_edit.setPlaceholderText("LSTD-XXXX-XXXX-…")
        layout.addWidget(self.key_edit, 1)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.ACCENT_HOVER};")
        layout.addWidget(self.error_label)
        self.activate_btn = QPushButton("Активировать")
        self.activate_btn.setObjectName("Primary")
        self.activate_btn.clicked.connect(self._activate)
        layout.addWidget(self.activate_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _activate(self) -> None:
        key = self.key_edit.toPlainText().strip()
        if not key:
            self.error_label.setText("Введите ключ.")
            return
        try:
            self.license_info = validate_key(key, product=self._product)
        except LicenseError as exc:
            self.error_label.setText(str(exc))
            return
        save_activation(key)
        self.accept()


def ensure_activated(
    parent: QWidget | None = None, *, product: int = PRODUCT_LOOP
) -> LicenseInfo | None:
    """Вернуть действующую лицензию; при её отсутствии показать окно активации.

    None — пользователь отказался: приложение должно завершиться,
    основной экран не открывается.
    """
    existing = load_valid_license(product=product)
    if existing is not None:
        return existing
    dialog = ActivationDialog(parent, product=product)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.license_info
    return None
