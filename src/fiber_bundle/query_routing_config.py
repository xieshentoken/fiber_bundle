"""Inactive M10 role configuration; no credentials, network, ledger or task access."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import InvalidInput
from .execution_config import ExecutionConfig, parse_execution_config
from .query_routing_files import read

SCHEMA = "query-routing-preparation/v1"
MAX_BYTES = 65536


@dataclass(frozen=True)
class LocalQueryConfig:
    image: str
    endpoint: str
    model: str
    secret_file: str | None
    max_input_tokens: int
    max_output_tokens: int
    max_request_bytes: int
    max_calls: int
    daily_calls: int
    timeout_seconds: int

    def validate(self) -> None:
        if not isinstance(self.image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image):
            raise InvalidInput("local_image_invalid")
        # Literal loopback only. Container-to-host connectivity needs separate qualification.
        if not isinstance(self.endpoint, str) or not re.fullmatch(
            r"http://(?:127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}/v1/chat/completions",
            self.endpoint,
        ):
            raise InvalidInput("local_endpoint_invalid")
        port = int(self.endpoint.rsplit(":", 1)[1].split("/", 1)[0])
        if port > 65535:
            raise InvalidInput("local_endpoint_invalid")
        if not isinstance(self.model, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", self.model
        ):
            raise InvalidInput("local_model_invalid")
        if self.secret_file is not None and (
            not isinstance(self.secret_file, str)
            or not self.secret_file
            or not Path(self.secret_file).is_absolute()
            or any(ord(c) < 32 for c in self.secret_file)
        ):
            raise InvalidInput("local_credential_reference_invalid")
        for field, maximum in (
            ("max_input_tokens", 200000),
            ("max_output_tokens", 8192),
            ("max_request_bytes", 262144),
            ("max_calls", 100),
            ("daily_calls", 1000),
            ("timeout_seconds", 600),
        ):
            value = getattr(self, field)
            if type(value) is not int or not 0 < value <= maximum:
                raise InvalidInput("local_limit_invalid")
        if self.max_calls > self.daily_calls:
            raise InvalidInput("local_budget_invalid")


@dataclass(frozen=True)
class QueryRoutingConfig:
    local_query: LocalQueryConfig | None
    cloud_answer: ExecutionConfig | None
    approved_backups: tuple[ExecutionConfig, ...]

    def validate(self) -> None:
        if (
            (self.local_query is not None and not isinstance(self.local_query, LocalQueryConfig))
            or (
                self.cloud_answer is not None and not isinstance(self.cloud_answer, ExecutionConfig)
            )
            or not isinstance(self.approved_backups, tuple)
            or any(not isinstance(p, ExecutionConfig) for p in self.approved_backups)
        ):
            raise InvalidInput("routing_configuration_type_invalid")
        value = asdict(self)
        value["approved_backups"] = list(value["approved_backups"])
        parse({"schema": SCHEMA, "enabled": False, **value})

    def answer_policy(self, backup_index: int | None = None) -> ExecutionConfig:
        """Resolve an explicit declaration for offline preparation, never a runtime switch."""
        if self.cloud_answer is None:
            raise InvalidInput("cloud_answer_not_configured")
        if backup_index is None:
            return self.cloud_answer
        if type(backup_index) is not int or not 1 <= backup_index <= len(self.approved_backups):
            raise InvalidInput("cloud_backup_index_invalid")
        return self.approved_backups[backup_index - 1]

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def inspect(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "state": "prepared" if self.local_query else "incomplete",
            "configuration_sha256": self.digest,
            "local_configured": self.local_query is not None,
            "cloud_answer_configured": self.cloud_answer is not None,
            "declared_backup_count": len(self.approved_backups),
            "activation_allowed": False,
            "runtime_verified": False,
            "local_failure_action": "stop",
            "cloud_input_contract": "local-answer/v1",
            "original_evidence_to_cloud": False,
            "credentials_read": False,
            "model_calls": 0,
        }


def _cloud(value: object) -> ExecutionConfig:
    if not isinstance(value, dict):
        raise InvalidInput("cloud_configuration_invalid")
    try:
        result = parse_execution_config(value)
        result.validate()
    except (TypeError, ValueError) as exc:
        raise InvalidInput("cloud_configuration_invalid") from exc
    if (
        result.max_attempts != 1
        or result.max_calls > result.daily_calls
        or not result.reserve_nusd <= result.task_limit_nusd <= result.daily_limit_nusd
    ):
        raise InvalidInput("cloud_budget_or_retry_invalid")
    return result


def parse(value: object) -> QueryRoutingConfig:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "enabled",
        "local_query",
        "cloud_answer",
        "approved_backups",
    }:
        raise InvalidInput("routing_fields_invalid")
    if value["schema"] != SCHEMA or value["enabled"] is not False:
        raise InvalidInput("routing_preparation_only")
    local = None
    if value["local_query"] is not None:
        if not isinstance(value["local_query"], dict):
            raise InvalidInput("local_configuration_invalid")
        try:
            local = LocalQueryConfig(**value["local_query"])
            local.validate()
        except (TypeError, ValueError) as exc:
            raise InvalidInput("local_configuration_invalid") from exc
    cloud = _cloud(value["cloud_answer"]) if value["cloud_answer"] is not None else None
    backups = value["approved_backups"]
    if not isinstance(backups, list) or len(backups) > 4 or (backups and cloud is None):
        raise InvalidInput("backup_list_invalid")
    policies = tuple(_cloud(item) for item in backups)
    # JSON permits escaped lone surrogates; they cannot become valid UTF-8 configuration.
    try:
        json.dumps(value, ensure_ascii=False).encode()
    except UnicodeError as exc:
        raise InvalidInput("routing_unicode_invalid") from exc
    identities = [(p.endpoint, p.model) for p in ((cloud,) if cloud else ()) + policies]
    if len(identities) != len(set(identities)):
        raise InvalidInput("duplicate_cloud_model")
    return QueryRoutingConfig(local, cloud, policies)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InvalidInput("duplicate_configuration_field")
        value[key] = item
    return value


def load(path: Path) -> QueryRoutingConfig:
    raw = read(path, MAX_BYTES)
    try:
        return parse(json.loads(raw, object_pairs_hook=_object))
    except (ValueError, RecursionError) as exc:
        raise InvalidInput("routing_json_invalid") from exc
