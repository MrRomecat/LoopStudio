"""Кладёт ffmpeg.exe и ffprobe.exe в bin/.

Сначала пробует скопировать бинарники из системного PATH (быстро, оффлайн).
Если в системе их нет — скачивает сборку с gyan.dev.

БЕЗОПАСНОСТЬ ПОСТАВКИ: эти exe уезжают в платный дистрибутив — единственная
точка притока стороннего кода. Поэтому версия закреплена, а скачанный архив
сверяется по SHA-256. При обновлении версии: возьмите новый versioned-URL и
его контрольную сумму со страницы https://www.gyan.dev/ffmpeg/builds/
(файл .sha256 рядом с архивом) и обновите обе константы.

Запуск:  python scripts/fetch_ffmpeg.py --sha256 <эталонная сумма>
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

#: закреплённый релиз (НЕ «latest»: содержимое latest меняется молча)
DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0-essentials_build.zip"
#: SHA-256 архива выше; пусто = скрипт потребует --sha256 (скачивание вслепую запрещено)
PINNED_SHA256 = ""
BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
BINARIES = ("ffmpeg", "ffprobe")


def _copy_from_path() -> bool:
    found = {name: shutil.which(name) for name in BINARIES}
    if not all(found.values()):
        return False
    BIN_DIR.mkdir(exist_ok=True)
    for name, src in found.items():
        assert src is not None
        dst = BIN_DIR / f"{name}.exe"
        shutil.copy2(src, dst)
        print(f"Скопирован {src} -> {dst}")
    return True


def _download(expected_sha256: str) -> None:
    BIN_DIR.mkdir(exist_ok=True)
    print(f"Скачиваю {DOWNLOAD_URL} (~90 МБ)...")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        urllib.request.urlretrieve(DOWNLOAD_URL, archive)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise SystemExit(
                "ОШИБКА: контрольная сумма архива не совпала!\n"
                f"  ожидалась: {expected_sha256}\n"
                f"  получена:  {actual}\n"
                "Архив подменён или битый — бинарники НЕ установлены."
            )
        print("SHA-256 совпала. Распаковываю...")
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                filename = Path(member).name
                if filename in {f"{b}.exe" for b in BINARIES}:
                    with zf.open(member) as src, open(BIN_DIR / filename, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"Извлечён {filename} -> {BIN_DIR / filename}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sha256",
        default=PINNED_SHA256,
        help="эталонная SHA-256 архива (обязательна, если не зашита в скрипт)",
    )
    args = parser.parse_args()
    have = [b for b in BINARIES if (BIN_DIR / f"{b}.exe").is_file()]
    if len(have) == len(BINARIES):
        print(f"ffmpeg и ffprobe уже лежат в {BIN_DIR} — ничего делать не нужно.")
        return 0
    if _copy_from_path():
        return 0
    if not args.sha256:
        print(
            "ОШИБКА: не задана эталонная SHA-256 архива.\n"
            "Возьмите сумму со страницы gyan.dev (файл .sha256 рядом с архивом) "
            "и запустите:  python scripts/fetch_ffmpeg.py --sha256 <сумма>",
            file=sys.stderr,
        )
        return 1
    _download(args.sha256)
    missing = [b for b in BINARIES if not (BIN_DIR / f"{b}.exe").is_file()]
    if missing:
        print(f"ОШИБКА: не удалось получить: {', '.join(missing)}", file=sys.stderr)
        return 1
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
