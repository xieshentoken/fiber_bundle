"""Inactive Pi document policy and declared formats; never load keys or probe providers."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .execution_config import ExecutionConfig, parse_execution_config
from .inputs import canonical
from .objects import digest
from .query_routing_files import read

SCHEMA = "document-creation-preparation/v1"
MAX_BYTES = 65536


@dataclass(frozen=True)
class CreationExecutionConfig:
    model_config: ExecutionConfig | None
    capability_revision: str | None
    formats: tuple[str, ...]

    def value(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "enabled": False,
            "executor": "pi",
            "model_config": asdict(self.model_config) if self.model_config is not None else None,
            "capability_revision": self.capability_revision,
            "formats": list(self.formats),
        }

    def validate(self) -> None:
        if (
            self.model_config is not None and not isinstance(self.model_config, ExecutionConfig)
        ) or not isinstance(self.formats, tuple):
            raise InvalidInput("document_configuration_invalid")
        parse(self.value())

    @property
    def sha256(self) -> str:
        self.validate()
        return digest(canonical(self.value()).encode())

    def policy(self) -> ExecutionConfig:
        self.validate()
        if self.model_config is None:
            raise InvalidInput("document_executor_not_configured")
        return self.model_config

    def inspect(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "state": "prepared" if self.model_config is not None else "incomplete",
            "configuration_sha256": self.sha256,
            "executor": "pi",
            "declared_formats": list(self.formats),
            "capability_verified": False,
            "runtime_verified": False,
            "execution_allowed": False,
            "automatic_switch": False,
            "credentials_read": False,
            "model_calls": 0,
        }


def parse(value: object) -> CreationExecutionConfig:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "enabled",
        "executor",
        "model_config",
        "capability_revision",
        "formats",
    }:
        raise InvalidInput("document_configuration_fields_invalid")
    if value["schema"] != SCHEMA or value["enabled"] is not False or value["executor"] != "pi":
        raise InvalidInput("document_configuration_preparation_only")
    formats = value["formats"]
    revision = value["capability_revision"]
    if value["model_config"] is None:
        if formats != [] or revision is not None:
            raise InvalidInput("document_configuration_incomplete")
        return CreationExecutionConfig(None, None, ())
    if not isinstance(value["model_config"], dict):
        raise InvalidInput("document_model_configuration_invalid")
    try:
        policy = parse_execution_config(value["model_config"])
        policy.validate()
    except (TypeError, ValueError, OverflowError):
        raise InvalidInput("document_model_configuration_invalid") from None
    if (
        policy.max_attempts != 1
        or policy.max_calls > policy.daily_calls
        or not policy.reserve_nusd <= policy.task_limit_nusd <= policy.daily_limit_nusd
    ):
        raise InvalidInput("document_budget_or_retry_invalid")
    if (
        not isinstance(formats, list)
        or not 1 <= len(formats) <= 16
        or any(
            not isinstance(f, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", f) is None
            for f in formats
        )
        or len(set(formats)) != len(formats)
        or not isinstance(revision, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", revision) is None
    ):
        raise InvalidInput("document_capability_declaration_invalid")
    try:
        canonical(value).encode()
    except UnicodeError:
        raise InvalidInput("document_configuration_unicode_invalid") from None
    return CreationExecutionConfig(policy, revision, tuple(formats))


def load(path: Path) -> CreationExecutionConfig:
    raw = read(path, MAX_BYTES)
    try:
        return parse(
            json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        )
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("document_configuration_json_invalid") from None
