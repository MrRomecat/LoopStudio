"""RenderJob — вся конфигурация ролика одним dataclass'ом.

Это одновременно:
- контракт между UI и движком (UI не знает про ffmpeg, движок — про Qt);
- формат проекта ``.lsproj`` (JSON, версионируемый, обратно совместимый).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.fsio import atomic_write_text

SCHEMA_VERSION = 3  # v3: + плавный старт, трим тишины, точная нормализация,
# исключение треков (v2: + package_folder)

Resolution = Literal["720p", "1080p", "1440p", "2160p"]
Quality = Literal["source", "high", "standard"]

RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2160p": (3840, 2160),
}

#: качество → (CRF видео, битрейт AAC)
QUALITY_PRESETS: dict[str, tuple[int, str]] = {
    "source": (16, "320k"),
    "high": (18, "320k"),
    "standard": (21, "192k"),
}


class JobError(ValueError):
    """Некорректная конфигурация задания (понятное сообщение для пользователя)."""


def _clean_fields(cls_obj: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Оставить только известные поля с корректными типами.

    Поле неверного типа отбрасывается (останется значение по умолчанию) —
    частично битый .lsproj открывается, а не роняет приложение. Целые числа
    мягко приводятся к float там, где ожидается float (JSON пишет 2 вместо 2.0).
    """
    defaults = cls_obj()
    clean: dict[str, Any] = {}
    for name, value in payload.items():
        if name not in cls_obj.__dataclass_fields__:
            continue  # неизвестные ключи будущих версий не роняют загрузку
        default = getattr(defaults, name)
        if isinstance(default, bool):
            if isinstance(value, bool):
                clean[name] = value
        elif isinstance(default, float):
            if isinstance(value, int | float) and not isinstance(value, bool):
                clean[name] = float(value)
        elif isinstance(default, int):
            if isinstance(value, int) and not isinstance(value, bool):
                clean[name] = value
        elif isinstance(default, str):
            if isinstance(value, str):
                clean[name] = value
        elif (
            isinstance(default, list)
            and isinstance(value, list)
            and all(isinstance(x, str) for x in value)
        ):
            clean[name] = value
    return clean


@dataclass(slots=True)
class AmbientConfig:
    """Один эмбиент-шар (шаг 3)."""

    enabled: bool = False
    path: str = ""
    volume: float = 0.5


@dataclass(slots=True)
class EffectsConfig:
    """Зацикливание и эффекты (шаг 4)."""

    seamless_loop: bool = True
    loop_crossfade: float = 1.0
    ken_burns: bool = False
    lut: str = ""  # "" = выключено; warm/cold/night/vhs/sepia
    overlay_path: str = ""
    overlay_opacity: float = 0.5
    overlay_screen_blend: bool = True
    vignette: bool = False
    vignette_strength: float = 0.3
    film_grain: bool = False
    film_grain_strength: int = 8


@dataclass(slots=True)
class RenderJob:
    """Полная конфигурация одного ролика (шаги 1–10 экрана)."""

    # Шаг 1 — видео-источник
    video_path: str = ""
    intro_path: str = ""
    outro_path: str = ""
    source_audio: bool = False
    source_audio_volume: float = 0.5

    # Шаг 2 — музыка
    music_dir: str = ""
    track_order: list[str] = field(default_factory=list)  # пустой = авто-сортировка
    excluded_tracks: list[str] = field(default_factory=list)  # видны, но не рендерятся
    fade_out: float = 0.0
    pause: float = 0.0
    crossfade: float = 0.0
    normalize: bool = False
    normalize_precise: bool = False  # двухпроходная (замер → линейная коррекция)
    trim_silence: bool = False  # срезать тишину на краях треков (в конвейере)

    # Шаг 3 — звуковой микс
    music_volume: float = 1.0
    ambient: list[AmbientConfig] = field(default_factory=list)

    # Шаг 4 — зацикливание и эффекты
    effects: EffectsConfig = field(default_factory=EffectsConfig)
    #: плавный старт: видео из чёрного + fade-in музыки и эмбиента;
    #: при паузах между треками — мягкое вступление каждого трека
    smooth_start: bool = False
    smooth_start_sec: float = 2.0

    # Шаг 5 — качество
    resolution: Resolution = "1080p"
    quality: Quality = "high"

    # Шаг 6 — длительность
    duration_sec: float = 3600.0

    # Шаги 7–8 — сохранение и название
    output_dir: str = ""
    #: каждый ролик — в своей подпапке (mp4 + треклист + паспорт + проект):
    #: серийный день не сваливает файлы пяти каналов в одну кучу
    package_folder: bool = True
    title_template: str = "LoopStudio {date}"

    # Шаг 9 — метаданные
    clean_metadata: bool = True

    schema_version: int = SCHEMA_VERSION

    # --- сериализация -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenderJob:
        """Собрать задание из словаря. Терпит чужие/битые данные:

        неизвестные ключи и поля неверного типа отбрасываются (значение по
        умолчанию), структурный мусор даёт JobError — но никогда не крэш.
        """
        if not isinstance(data, dict):
            raise JobError("Данные проекта повреждены (ожидался объект настроек).")
        payload = dict(data)
        payload.pop("schema_version", None)
        ambient_raw = payload.pop("ambient", [])
        effects_raw = payload.pop("effects", {})
        if not isinstance(ambient_raw, list) or not all(
            isinstance(item, dict) for item in ambient_raw
        ):
            raise JobError("Данные проекта повреждены (раздел эмбиент-слоёв).")
        if not isinstance(effects_raw, dict):
            raise JobError("Данные проекта повреждены (раздел эффектов).")
        job = cls(**_clean_fields(cls, payload))
        job.ambient = [AmbientConfig(**_clean_fields(AmbientConfig, item)) for item in ambient_raw]
        job.effects = EffectsConfig(**_clean_fields(EffectsConfig, effects_raw))
        return job

    def save(self, path: Path) -> None:
        atomic_write_text(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path) -> RenderJob:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JobError(f"Не удалось открыть проект «{path.name}»: {exc}") from exc
        if not isinstance(data, dict):
            raise JobError(f"Файл «{path.name}» не похож на проект LoopStudio.")
        return cls.from_dict(data)

    # --- валидация ----------------------------------------------------------

    def validate(self, *, require_music: bool = True) -> None:
        """Проверить, что задание можно рендерить. Бросает JobError.

        :param require_music: False — пакетный режим проверяет базу без музыки,
            папки подставляются клонами позже.
        """
        if not self.video_path or not Path(self.video_path).is_file():
            raise JobError("Не выбрано основное видео (шаг 1).")
        if require_music and (not self.music_dir or not Path(self.music_dir).is_dir()):
            raise JobError("Не выбрана папка с музыкой (шаг 2).")
        if not self.output_dir:
            raise JobError("Не выбрана папка сохранения (шаг 7).")
        if self.duration_sec < 60 or self.duration_sec > 36_000:
            raise JobError("Длительность ролика — от 1 минуты до 10 часов (шаг 6).")
        if self.resolution not in RESOLUTIONS:
            raise JobError(f"Неизвестное разрешение: {self.resolution}")
        if self.quality not in QUALITY_PRESETS:
            raise JobError(f"Неизвестный режим качества: {self.quality}")
        for clip_field, label in ((self.intro_path, "интро"), (self.outro_path, "аутро")):
            if clip_field and not Path(clip_field).is_file():
                raise JobError(f"Файл {label} не найден: {clip_field}")
        for i, layer in enumerate(self.ambient, start=1):
            if layer.enabled and (not layer.path or not Path(layer.path).is_file()):
                raise JobError(f"Эмбиент-слой {i} включён, но файл не выбран (шаг 3).")
        if self.effects.overlay_path and not Path(self.effects.overlay_path).is_file():
            raise JobError(f"Файл оверлея не найден: {self.effects.overlay_path}")
