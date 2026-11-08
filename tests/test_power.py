"""core/power.py: keep-awake и выключение — только через моки, без реальных вызовов."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from core import power


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
def test_keep_awake_sets_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeKernel32:
        @staticmethod
        def SetThreadExecutionState(flags: int) -> int:
            calls.append(flags)
            return 1

    class FakeWinDLL:
        kernel32 = FakeKernel32()

    monkeypatch.setattr("core.power.ctypes.windll", FakeWinDLL(), raising=False)
    assert power.keep_system_awake(True) is True
    assert power.keep_system_awake(False) is True
    assert calls[0] == power._ES_CONTINUOUS | power._ES_SYSTEM_REQUIRED
    assert calls[1] == power._ES_CONTINUOUS


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
def test_shutdown_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(cmd)
        assert kwargs.get("stdin") == subprocess.DEVNULL, "stdin обязан быть DEVNULL"
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("core.power.subprocess.run", fake_run)
    assert power.schedule_shutdown(60) is True
    assert power.cancel_shutdown() is True
    # абсолютный путь System32 — защита от подложенного рядом shutdown.exe
    assert seen[0][0].lower().endswith("\\system32\\shutdown.exe")
    assert seen[0][1:] == ["/s", "/t", "60"]
    assert seen[1][1:] == ["/a"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
def test_shutdown_failure_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr("core.power.subprocess.run", fake_run)
    assert power.schedule_shutdown(10) is False
