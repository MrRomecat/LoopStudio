"""Генерация PDF-инструкции установки MuseStudio (QPdfWriter, без зависимостей).

Запуск из корня: .venv\\Scripts\\python.exe scripts\\make_muse_manual.py
Результат: docs/musestudio/MuseStudio-УСТАНОВКА.pdf — кладётся рядом с
установщиком при передаче покупателю.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_HTML = """
<h1>MuseStudio — установка и первый запуск</h1>
<p><i>Генератор профессиональных промтов для ИИ-музыки: трек Suno, текст песни
и раскадровка клипа. Windows 10/11, 64-bit.</i></p>

<h2>1. Установка</h2>
<ol>
<li>Запустите <b>MuseStudio-1.0.0-setup.exe</b> и следуйте шагам мастера.</li>
<li>После установки запустите MuseStudio с рабочего стола или из меню «Пуск».</li>
</ol>

<h2>2. Активация</h2>
<ol>
<li>При первом запуске появится окно активации.</li>
<li>Вставьте лицензионный ключ из письма о покупке (начинается с
<b>LSTD-…</b>) и нажмите «Активировать».</li>
<li>Активация привязывает ключ к этому компьютеру и дальше работает
полностью оффлайн. Ключи других продуктов Studio Suite на этой же машине
не затираются.</li>
</ol>

<h2>3. Ключ OpenRouter (для настоящих генераций)</h2>
<ol>
<li>Зарегистрируйтесь на <b>openrouter.ai</b> и создайте API-ключ
(вида sk-or-…), пополните баланс на пару долларов.</li>
<li>В MuseStudio откройте «Настройки → Генерация и API-ключ…», вставьте ключ
и нажмите «Сохранить». Ключ хранится в Диспетчере учётных данных Windows
и не попадает в файлы.</li>
<li>Без ключа доступен оффлайн-режим с шаблонными заготовками — можно
осмотреться в программе.</li>
</ol>

<h2>4. Первый трек за 3 минуты</h2>
<ol>
<li><b>Стиль</b>: выберите, например, «Lo-Fi» (или создайте свой).</li>
<li><b>Шаг 1 · Музыка</b>: «Сгенерировать промт трека» → скопируйте поля
Style и Lyrics в Suno; рядом — Exclude Styles и рекомендации ползунков.</li>
<li><b>Шаг 2 · Лирика</b>: тема песни (например, «осенний дождь за окном»),
при желании — детали и язык; режим A/B даёт два варианта на выбор.</li>
<li><b>Шаг 3 · Клип</b>: длительность трека (3:00), динамика «Плавный»,
модель Veo → «Сгенерировать раскадровку» — таблица кадров с таймкодами.</li>
<li><b>Экспорт</b>: «Собрать пакет» → все файлы в
Документы\\MuseStudio\\exports.</li>
</ol>

<h2>5. Полезное</h2>
<ul>
<li>Каждая правка промта стиля сохраняется новой версией — «История» вернёт
любую; «золотая» отметка помечает лучшую.</li>
<li>Работа автосохраняется; «Проект → Сохранить проект…» пишет файл
.msproj.</li>
<li>Журнал приложения: «Проект → Открыть журнал приложения».</li>
</ul>
"""


def main() -> int:
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    target = Path(__file__).resolve().parents[1] / "docs" / "musestudio"
    target.mkdir(parents=True, exist_ok=True)
    pdf_path = target / "MuseStudio-УСТАНОВКА.pdf"
    writer = QPdfWriter(str(pdf_path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 12, 15, 12), QPageLayout.Unit.Millimeter)
    writer.setTitle("MuseStudio — установка")
    document = QTextDocument()
    document.setHtml(_HTML)
    document.print_(writer)
    print(f"OK {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
