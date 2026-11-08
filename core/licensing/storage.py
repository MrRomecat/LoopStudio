"""Хранение активации: зашифрованный license.dat в %APPDATA%/LoopStudio.

Ключ шифрования выводится из отпечатка машины — файл, скопированный на другой
компьютер, просто не расшифруется. Это и есть привязка к машине.

Машина может держать НЕСКОЛЬКО ключей (клиент купил LoopStudio и
PreviuStudio отдельными ключами): новые активации добавляются к списку,
а не затирают друг друга. Совместимость в обе стороны: старый license.dat
(поле «key») читается; в новом формате поле «key» тоже пишется — его
поймут даже версии LoopStudio, выпущенные до линейки продуктов.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from core.fsio import atomic_write_bytes
from core.licensing.validate import (
    PRODUCT_LOOP,
    LicenseError,
    LicenseInfo,
    machine_fingerprint,
    parse_key,
    validate_key,
)
from core.settings import settings_dir

_SALT = b"loopstudio-license-v1"
_MAX_KEYS = 10  # разумный предел: больше продуктов линейка не предполагает


def _license_file() -> Path:
    return settings_dir() / "license.dat"


def _fernet(fingerprint: str) -> Fernet:
    digest = hashlib.sha256(fingerprint.encode("utf-8") + _SALT).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def save_activation(key: str, *, fingerprint: str | None = None) -> None:
    """Добавить активацию к списку машины (ключ уже проверен вызывающим кодом).

    Прежние ключи сохраняются: активация PreviuStudio не отключает LoopStudio.
    """
    fp = fingerprint or machine_fingerprint()
    keys = [stored for stored in load_activations(fingerprint=fp) if stored != key]
    keys.insert(0, key)
    keys = keys[:_MAX_KEYS]
    payload = json.dumps(
        {
            "key": _legacy_key(keys),  # это поле читают старые версии LoopStudio
            "keys": keys,
            "fingerprint": fp,
            "activated_at": datetime.now(UTC).isoformat(),
        }
    ).encode("utf-8")
    atomic_write_bytes(_license_file(), _fernet(fp).encrypt(payload))


def _legacy_key(keys: list[str]) -> str:
    """Ключ для старого поля «key»: первый с битом LOOP, иначе первый."""
    for key in keys:
        try:
            if parse_key(key).has_product(PRODUCT_LOOP):
                return key
        except LicenseError:
            continue
    return keys[0] if keys else ""


def load_activations(*, fingerprint: str | None = None) -> list[str]:
    """Все сохранённые ключи машины. Пусто — активации нет или файл чужой."""
    file = _license_file()
    if not file.is_file():
        return []
    fp = fingerprint or machine_fingerprint()
    try:
        payload = _fernet(fp).decrypt(file.read_bytes())
        data = json.loads(payload)
    except (InvalidToken, OSError, json.JSONDecodeError, ValueError):
        return []
    if data.get("fingerprint") != fp:
        return []
    raw_keys = data.get("keys")
    if isinstance(raw_keys, list):
        return [key for key in raw_keys if isinstance(key, str) and key]
    legacy = data.get("key")  # формат до линейки: один ключ
    return [legacy] if isinstance(legacy, str) and legacy else []


def load_activation(*, fingerprint: str | None = None) -> str | None:
    """Первый сохранённый ключ (совместимость со старыми вызовами)."""
    keys = load_activations(fingerprint=fingerprint)
    return keys[0] if keys else None


def load_valid_license(
    *, public_key: bytes | None = None, fingerprint: str | None = None, product: int = 0
) -> LicenseInfo | None:
    """Действующая лицензия нужного продукта среди ключей машины.

    ``product`` — бит запускаемого модуля линейки (0 = не проверять).
    None — подходящего ключа нет: нужно окно активации.
    """
    for key in load_activations(fingerprint=fingerprint):
        try:
            return validate_key(key, public_key=public_key, product=product)
        except LicenseError:
            continue
    return None


def clear_activation() -> None:
    """Сброс активации (поддержка: перенос лицензии на другую машину)."""
    _license_file().unlink(missing_ok=True)
