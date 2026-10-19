"""Validated video extraction choices; credentials stay in the shared model catalog."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .payloads import Payload

DEFAULT_TERMS = (
    "API",
    "ASR",
    "CPU",
    "GPU",
    "LLM",
    "Python",
    "Obsidian",
    "Bilibili",
    "YouTube",
    "Fiber bundle",
    "Hermes",
    "Pi",
)


class Settings(Payload):
    skill_path: str | None = None
    python_path: str | None = None
    asr_python: str | None = None
    tool_directories: list[str] = Field(default_factory=list, max_length=20)
    vision_model_configuration_id: str | None = Field(default=None, max_length=200)
    asr_backend: Literal["local-qwen3", "transcription-api"] = "local-qwen3"
    asr_model: str = Field(default="Qwen/Qwen3-ASR-0.6B", min_length=1, max_length=2000)
    asr_language: str = Field(default="auto", min_length=1, max_length=40)
    asr_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    asr_dtype: Literal["auto", "float32", "float16", "bfloat16"] = "auto"
    asr_api_model_configuration_id: str | None = Field(
        default=None, pattern=r"^model_[a-f0-9]{32}$"
    )
    asr_api_response_format: Literal["json", "verbose_json"] = "json"
    asr_api_reserve_tokens: int = Field(default=16384, ge=1, le=200000)
    default_terms: list[str] = Field(default_factory=lambda: list(DEFAULT_TERMS), max_length=2000)
    max_media_bytes: int = Field(default=4 * 1024**3, ge=16 * 1024**2, le=32 * 1024**3)
    timeout_seconds: int = Field(default=21600, ge=60, le=43200)

    @field_validator("tool_directories")
    @classmethod
    def tool_paths(cls, value: list[str]) -> list[str]:
        if any(not Path(p).is_absolute() or ":" in p or not Path(p).is_dir() for p in value):
            raise ValueError("video_skill_tool_directory_invalid")
        return list(dict.fromkeys(value))

    @field_validator("asr_model", "asr_language")
    @classmethod
    def model_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("video_asr_model_invalid")
        return value

    @field_validator("vision_model_configuration_id")
    @classmethod
    def vision_reference(cls, value: str | None) -> str | None:
        import re

        if (
            value is not None
            and value != "app-default"
            and not re.fullmatch(r"model_[a-f0-9]{32}", value)
        ):
            raise ValueError("video_vision_model_invalid")
        return value

    @model_validator(mode="after")
    def api_selection(self) -> "Settings":
        if self.asr_backend == "transcription-api" and not self.asr_api_model_configuration_id:
            raise ValueError("video_asr_api_model_required")
        return self
