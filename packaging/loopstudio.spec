# -*- mode: python ; coding: utf-8 -*-
# Сборка: .venv\Scripts\pyinstaller packaging\loopstudio.spec --noconfirm
# Результат: dist/LoopStudio/ (onedir) — все файлы рядом с exe,
# ffmpeg кладётся в bin/ рядом с LoopStudio.exe (так его ищет core.media.ffmpeg).

from pathlib import Path

ROOT = Path(SPECPATH).parent

# Обязательные части поставки проверяются ЖЁСТКО: тихая сборка без ffmpeg
# или без библиотеки ассетов давала бы мёртвый дистрибутив у покупателя.
binaries = []
for name in ("ffmpeg.exe", "ffprobe.exe"):
    source = ROOT / "bin" / name
    if not source.is_file():
        raise SystemExit(
            f"СБОРКА ОСТАНОВЛЕНА: нет {source}. "
            "Запустите scripts/fetch_ffmpeg.py и повторите."
        )
    binaries.append((str(source), "bin"))

datas = []
assets = ROOT / "assets"
if not (assets / "ambient").is_dir() or not any((assets / "ambient").iterdir()):
    raise SystemExit(
        "СБОРКА ОСТАНОВЛЕНА: пустая встроенная библиотека assets/ambient. "
        "Запустите scripts/generate_assets.py и повторите."
    )
datas.append((str(assets), "assets"))
licenses = ROOT / "packaging" / "licenses"
if not (licenses / "NOTICE.txt").is_file():
    raise SystemExit(
        "СБОРКА ОСТАНОВЛЕНА: нет packaging/licenses/NOTICE.txt — "
        "лицензии сторонних компонентов обязаны ехать в дистрибутив."
    )
datas.append((str(licenses), "licenses"))

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "tests", "tools", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="LoopStudio",
    icon=str(ROOT / "assets" / "icon.ico") if (ROOT / "assets" / "icon.ico").is_file() else None,
    console=False,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="LoopStudio",
)
