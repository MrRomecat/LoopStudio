"""Автопроверка готового ролика: «Готово» больше не говорится про битый файл.

Три быстрых проверки (секунды даже на 7-часовом файле):
1. ffprobe: видео- и аудиопоток на месте;
2. длительность файла совпадает с расчётом таймлайна (допуск на контейнер);
3. хвост файла реально декодируется (``-sseof``) — ловит оборванную запись.

Модуль только читает готовый файл — конвейер рендера не меняется.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from core.media.ffmpeg import FFmpegCancelled, FFmpegError, run_ffmpeg
from core.media.probe import ProbeError, probe

_DURATION_TOLERANCE_SEC = 1.5
_TAIL_SECONDS = 3


class VerifyError(RuntimeError):
    """Готовый файл не прошёл проверку (человекочитаемое сообщение)."""


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Итог проверки готового файла."""

    ok: bool
    checks: tuple[str, ...]  # пройденные проверки («Видео- и аудиопоток на месте»)
    problems: tuple[str, ...]  # найденные проблемы
    file_duration: float | None  # фактическая длительность файла по ffprobe

    @property
    def summary(self) -> str:
        if self.ok:
            return "ПРОВЕРЕНО — файл целый"
        return "ПРОВЕРКА НЕ ПРОЙДЕНА: " + "; ".join(self.problems)


def verify_output(
    path: Path, expected_duration: float, *, cancel: threading.Event | None = None
) -> VerifyReport:
    """Проверить готовый файл. Не бросает исключений — возвращает отчёт.

    :param expected_duration: расчётная длительность ролика (таймлайн + интро/аутро).
    """
    checks: list[str] = []
    problems: list[str] = []
    file_duration: float | None = None

    try:
        info = probe(path)
    except ProbeError as exc:
        return VerifyReport(
            ok=False, checks=(), problems=(f"файл не читается ffprobe ({exc})",), file_duration=None
        )

    if info.has_video and info.has_audio:
        checks.append("Видео- и аудиопоток на месте")
    else:
        missing = "видеопоток" if not info.has_video else "аудиопоток"
        problems.append(f"в файле отсутствует {missing}")

    file_duration = info.duration
    delta = abs(info.duration - expected_duration)
    if delta <= _DURATION_TOLERANCE_SEC:
        checks.append(f"Длительность файла совпадает с расчётом (±{_DURATION_TOLERANCE_SEC:g} с)")
    else:
        problems.append(
            f"длительность файла ({info.duration:.1f} с) не совпадает с расчётной "
            f"({expected_duration:.1f} с) — файл мог записаться не полностью"
        )

    if cancel is not None and cancel.is_set():
        raise FFmpegCancelled("Операция отменена пользователем.")

    try:
        run_ffmpeg(
            [
                "-sseof",
                str(-_TAIL_SECONDS),
                "-i",
                str(path),
                "-t",
                str(_TAIL_SECONDS),
                "-f",
                "null",
                "-",
            ],
            cancel=cancel,
        )
        checks.append("Хвост файла читается (последние секунды декодируются)")
    except FFmpegError:
        problems.append("хвост файла не декодируется — запись могла оборваться")

    return VerifyReport(
        ok=not problems, checks=tuple(checks), problems=tuple(problems), file_duration=file_duration
    )
