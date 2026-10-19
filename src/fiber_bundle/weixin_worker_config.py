"""Strict local startup settings; no credentials, binding changes or policy defaults."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config, InvalidInput, contained
from .weixin_events import Binding


@dataclass(frozen=True)
class WorkerConfig:
    binding: Binding
    execution_config: str = "configs/execution.example.json"
    enabled: bool = False
    maximum: int = 0
    poll_limit: int = 1
    poll_seconds: float = 1
    version: int = 1
    publication_maximum: int = 0

    def validate(self, config: Config) -> None:
        config.validate()
        if type(self.version) is not int or self.version != 1:
            raise InvalidInput("unsupported Weixin worker configuration")
        if not isinstance(self.binding, Binding):
            raise InvalidInput("explicit Weixin binding required")
        self.binding.validate()
        if type(self.enabled) is not bool:
            raise InvalidInput("worker enabled switch must be boolean")
        if type(self.maximum) is not int or not 0 <= self.maximum <= 100:
            raise InvalidInput("worker task limit must be an integer from 0 to 100")
        if type(self.publication_maximum) is not int or not 0 <= self.publication_maximum <= 100:
            raise InvalidInput("worker publication limit must be an integer from 0 to 100")
        if type(self.poll_limit) is not int or not 1 <= self.poll_limit <= 100:
            raise InvalidInput("worker poll limit must be an integer from 1 to 100")
        if (
            type(self.poll_seconds) not in (int, float)
            or not math.isfinite(self.poll_seconds)
            or not 0 < self.poll_seconds <= 300
        ):
            raise InvalidInput("worker poll interval must be greater than zero and at most 300")
        contained(config.project_root, self.execution_config)


def read_json(config: Config, path: Path) -> dict[str, Any]:
    """Bounded project-local regular file; never follow symlinks or echo file contents."""
    candidate = path if path.is_absolute() else config.project_root / path
    try:
        relative = candidate.relative_to(config.project_root).as_posix()
        safe = contained(config.project_root, relative)
        if not safe.is_file() or safe.stat().st_size > 65536:
            raise InvalidInput("invalid worker configuration file")
        with safe.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise InvalidInput("worker configuration too large")
        result = json.loads(raw)
    except (OSError, ValueError, TypeError):
        raise InvalidInput("cannot read worker configuration JSON") from None
    if not isinstance(result, dict):
        raise InvalidInput("worker configuration must be an object")
    return result


def load_worker_config(config: Config, path: Path) -> WorkerConfig:
    data = read_json(config, path)
    if set(data) - {"publication_maximum"} != {
        "version",
        "enabled",
        "binding",
        "execution_config",
        "maximum",
        "poll_limit",
        "poll_seconds",
    }:
        raise InvalidInput("invalid worker configuration fields")
    try:
        if not isinstance(data["binding"], dict):
            raise TypeError
        result = WorkerConfig(**{**data, "binding": Binding(**data["binding"])})
    except TypeError:
        raise InvalidInput("invalid worker binding fields") from None
    result.validate(config)
    return result
