"""Проверка лицензионных ключей Studio Suite (оффлайн, Ed25519).

Ключ: ``LSTD-`` + base32(payload + подпись Ed25519), группами по 4 символа.
Payload версии 1 (11 байт): версия, тариф, срок (дни unix, 0 = бессрочно), uid покупки —
такие ключи продавались до появления линейки и означают «только LoopStudio».
Payload версии 2 (12 байт): то же + байт products (битовая маска модулей линейки)
между сроком и uid. Один keygen и одна пара ключей на все продукты Studio Suite.
Подпись создаётся приватным ключом владельца (tools/keygen.py, НЕ в дистрибутиве);
приложение содержит только публичный ключ — подделка без приватника невозможна.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import platform
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

KEY_PREFIX = "LSTD"
PAYLOAD_LEN_V1 = 11
PAYLOAD_LEN_V2 = 12
SIGNATURE_LEN = 64
_EPOCH = date(1970, 1, 1)

#: Публичный ключ продукта (hex, 32 байта). Пара создаётся tools/keygen.py init.
PRODUCT_PUBLIC_KEY_HEX = "df20cb1681f8c10a90662d7d7aa2b48964aac44d1a569d6db5b897c0e34a2df3"

TIERS: dict[int, str] = {1: "starter", 2: "pro", 3: "studio"}

# --- продукты линейки (битовая маска в payload версии 2) ----------------------

PRODUCT_LOOP = 1  # LoopStudio — длинные музыкальные видео
PRODUCT_PRVU = 2  # PreviuStudio — упаковка роликов (превью/заголовок/описание)
PRODUCT_LONG = 4  # LongStudio (зарезервировано)
PRODUCT_SHRT = 8  # ShortsStudio (зарезервировано)
PRODUCT_MUSE = 16  # MuseStudio — промты ИИ-музыки (Suno + лирика + клип)

PRODUCT_TITLES: dict[int, str] = {
    PRODUCT_LOOP: "LoopStudio",
    PRODUCT_PRVU: "PreviuStudio",
    PRODUCT_LONG: "LongStudio",
    PRODUCT_SHRT: "ShortsStudio",
    PRODUCT_MUSE: "MuseStudio",
}


def product_names(products: int) -> str:
    """Человеческий список продуктов маски: «LoopStudio + PreviuStudio»."""
    titles = [title for bit, title in PRODUCT_TITLES.items() if products & bit]
    return " + ".join(titles) if titles else "—"


class LicenseError(Exception):
    """Ключ не подходит (понятное сообщение для пользователя)."""


@dataclass(frozen=True, slots=True)
class LicenseInfo:
    """Расшифрованное содержимое действительного ключа."""

    tier: str
    expires: date | None  # None = бессрочная лицензия
    uid: str
    products: int = PRODUCT_LOOP  # битовая маска модулей; ключи версии 1 = LOOP

    def has_product(self, product: int) -> bool:
        """Открывает ли ключ данный модуль линейки."""
        return bool(self.products & product)


def product_public_key() -> bytes:
    return bytes.fromhex(PRODUCT_PUBLIC_KEY_HEX)


def _clean(key: str) -> str:
    return re.sub(r"[\s-]+", "", key.strip().upper())


def parse_key(key: str, *, public_key: bytes | None = None) -> LicenseInfo:
    """Разобрать ключ и проверить подпись (без проверки срока)."""
    cleaned = _clean(key)
    if not cleaned.startswith(KEY_PREFIX):
        raise LicenseError("Это не лицензионный ключ Studio Suite: ключ начинается с LSTD-…")
    body = cleaned[len(KEY_PREFIX) :]
    try:
        pad = "=" * (-len(body) % 8)
        blob = base64.b32decode(body + pad)
    except (ValueError, TypeError) as exc:
        raise LicenseError("Ключ повреждён: проверьте, что он скопирован целиком.") from exc
    payload_len = len(blob) - SIGNATURE_LEN
    if payload_len not in (PAYLOAD_LEN_V1, PAYLOAD_LEN_V2):
        raise LicenseError("Ключ повреждён: неверная длина.")
    payload, signature = blob[:payload_len], blob[payload_len:]
    verifier = Ed25519PublicKey.from_public_bytes(public_key or product_public_key())
    try:
        verifier.verify(signature, payload)
    except InvalidSignature as exc:
        raise LicenseError("Ключ недействителен: подпись не совпадает.") from exc

    version = payload[0]
    if version == 1 and payload_len == PAYLOAD_LEN_V1:
        products, uid = PRODUCT_LOOP, payload[6:11]
    elif version == 2 and payload_len == PAYLOAD_LEN_V2:
        products, uid = payload[6], payload[7:12]
        if not products:
            raise LicenseError("Ключ повреждён: не указан ни один продукт.")
    else:
        raise LicenseError("Ключ выпущен для другой версии приложения. Обновите программу.")
    tier_code = payload[1]
    if tier_code not in TIERS:
        raise LicenseError("Ключ повреждён: неизвестный тариф.")
    days = int.from_bytes(payload[2:6], "big")
    expires = _EPOCH + timedelta(days=days) if days else None
    return LicenseInfo(tier=TIERS[tier_code], expires=expires, uid=uid.hex(), products=products)


def validate_key(
    key: str, *, public_key: bytes | None = None, today: date | None = None, product: int = 0
) -> LicenseInfo:
    """Полная проверка: подпись + срок действия (+ доступ к продукту, если задан).

    ``product`` — бит запускаемого приложения (PRODUCT_LOOP, PRODUCT_PRVU…);
    0 — принадлежность к продукту не проверять (keygen, сервисные проверки).
    """
    info = parse_key(key, public_key=public_key)
    current = today or datetime.now(UTC).date()
    if info.expires is not None and current > info.expires:
        raise LicenseError(
            f"Срок действия ключа истёк {info.expires.strftime('%d.%m.%Y')}. Продлите лицензию."
        )
    if product and not info.has_product(product):
        needed = PRODUCT_TITLES.get(product, "этот продукт")
        raise LicenseError(
            f"Ключ действителен, но не включает {needed}. "
            f"В нём: {product_names(info.products)}. Нужен ключ с модулем {needed}."
        )
    return info


def _machine_guid() -> str:
    """GUID установки Windows из реестра — не меняется от сети/VPN/переименований."""
    if sys.platform != "win32":
        return ""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except OSError:
        return ""


def _volume_serial() -> str:
    """Серийник тома C: системным вызовом — без запуска внешних команд."""
    if sys.platform != "win32":
        return ""
    serial = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p("C:\\"), None, 0, ctypes.byref(serial), None, None, None, 0
    )
    return f"{serial.value:08X}" if ok else ""


def machine_fingerprint() -> str:
    """Отпечаток машины: SHA256(GUID Windows + серийник тома + имя компьютера).

    Только стабильные источники, читаемые одинаково из консольного и оконного
    процесса. Никаких MAC-адресов (меняются от VPN/Wi-Fi) и subprocess-вызовов.
    """
    parts = f"{_machine_guid()}|{_volume_serial()}|{platform.node()}"
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()
