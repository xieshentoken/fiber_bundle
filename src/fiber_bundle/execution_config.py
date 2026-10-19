"""Explicit execution limits and secret loading; never inherited personal settings."""

import hashlib
import json
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fiber_bundle.config import InvalidInput


@dataclass(frozen=True)
class ExecutionConfig:
    image: str
    endpoint: str
    model: str
    secret_file: str
    price_revision: str
    input_nusd: int
    output_nusd: int
    max_input_tokens: int
    max_output_tokens: int
    max_request_bytes: int
    task_limit_nusd: int
    daily_limit_nusd: int
    max_calls: int
    daily_calls: int
    max_attempts: int
    timeout_seconds: int
    timezone: str
    currency: str = "USD"

    def validate(self) -> None:
        for field in (
            "image",
            "endpoint",
            "model",
            "secret_file",
            "price_revision",
            "timezone",
            "currency",
        ):
            if not isinstance(getattr(self, field), str):
                raise InvalidInput("invalid configuration field type")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image):
            raise InvalidInput("immutable executor image required")
        # M3 has one verified provider/protocol, never an arbitrary credential-bearing proxy.
        if self.endpoint != "https://opencode.ai/zen/go/v1/chat/completions":
            raise InvalidInput("unverified model endpoint")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}", self.model):
            raise InvalidInput("invalid model")
        if not self.price_revision or self.currency != "USD":
            raise InvalidInput("known USD pricing required")
        for key in (
            "input_nusd",
            "output_nusd",
            "max_input_tokens",
            "max_output_tokens",
            "max_request_bytes",
            "task_limit_nusd",
            "daily_limit_nusd",
            "max_calls",
            "daily_calls",
            "max_attempts",
            "timeout_seconds",
        ):
            value = getattr(self, key)
            if type(value) is not int or not 0 < value <= 10**12:
                raise InvalidInput("positive explicit limits required")
        if not 1 <= self.max_attempts <= 3 or not 1 <= self.timeout_seconds <= 600:
            raise InvalidInput("unbounded execution")
        if self.max_request_bytes > 262144 or self.max_output_tokens > 8192:
            raise InvalidInput("request limit too large")
        if self.max_input_tokens > 200000:
            raise InvalidInput("unverified context limit")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidInput("invalid billing timezone") from exc
        if not Path(self.secret_file).is_absolute():
            raise InvalidInput("secret path must be explicit")

    @property
    def reserve_nusd(self) -> int:
        return self.max_input_tokens * self.input_nusd + self.max_output_tokens * self.output_nusd

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def load_execution_config(path: Path) -> ExecutionConfig:
    try:
        data = json.loads(path.read_text())
        result = parse_execution_config(data)
        result.validate()
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise InvalidInput("invalid execution configuration") from exc


def read_secret(path: Path) -> str:
    """Support the explicitly supplied local RTF without logging it or making a plaintext copy."""
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 65536:
        raise InvalidInput("invalid credential file")
    if path.parent.name == "model-secrets" and path.suffix == ".json":
        from .model_configuration.secrets import load

        return load(path)
    if path.suffix.lower() == ".rtf":
        result = subprocess.run(
            ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode:
            raise InvalidInput("credential conversion failed")
        text: str = result.stdout.decode("utf-8")
    else:
        text = path.read_text()
    secret = text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", secret):
        raise InvalidInput("expected one credential")
    return secret


def parse_execution_config(data: dict[str, Any]) -> ExecutionConfig:
    if "configuration_id" in data:
        from .model_configuration.policy import RegisteredPolicy

        return RegisteredPolicy(**data)
    return ExecutionConfig(**data)
