"""Аппаратное кодирование: детект честный, выбранный кодер реально кодирует."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.media.encoders import detect_hw_encoder
from core.media.probe import probe
from core.video.looper import VideoEncodeSettings, build_looped_video


def test_detect_is_cached_and_valid() -> None:
    first = detect_hw_encoder()
    assert first == detect_hw_encoder()  # кэш стабилен
    assert first in (None, "h264_nvenc", "h264_qsv", "h264_amf")


def test_x264_args_unchanged() -> None:
    args = VideoEncodeSettings(width=640, height=360).encode_args()
    assert "-crf" in args and "libx264" in args


def test_hw_args_have_quality_control() -> None:
    """Шкала качества аппаратных кодеров сдвинута: cq/qp выше «crf» на 3–5."""
    nvenc = VideoEncodeSettings(width=640, height=360, codec="h264_nvenc", crf=18).encode_args()
    assert "-cq" in nvenc and "23" in nvenc
    qsv = VideoEncodeSettings(width=640, height=360, codec="h264_qsv", crf=21).encode_args()
    assert "-global_quality" in qsv and "24" in qsv


def test_hw_encoder_renders_playable_loop(video_5s: Path, tmp_path: Path) -> None:
    """Если аппаратный кодер найден — полный луп собирается и склеивается copy."""
    codec = detect_hw_encoder()
    if codec is None:
        pytest.skip("аппаратного кодера нет — работает fallback libx264")
    settings = VideoEncodeSettings(width=640, height=360, fps=30.0, codec=codec, crf=23)
    out = tmp_path / "hw.mp4"
    build_looped_video(video_5s, 20.0, out, settings, work_dir=tmp_path, crossfade=1.0)
    info = probe(out)
    assert info.duration == pytest.approx(20.0, abs=0.1)
    assert info.video_codec == "h264"
