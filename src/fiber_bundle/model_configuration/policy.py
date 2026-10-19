"""Versioned registered model policy; legacy dataclass bytes and digests stay unchanged."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import database
from ..config import Config, InvalidInput
from ..execution_config import ExecutionConfig
from . import providers, store
from .contracts import Connection, Protocol, Provider
from .errors import ModelError


@dataclass(frozen=True)
class RegisteredPolicy(ExecutionConfig):
    provider: Provider = "custom"
    protocol: Protocol = "openai-completions"
    effort: str = "default"
    configuration_id: str = ""
    configuration_revision: int = 1
    credential_generation: int = 1

    def validate(self) -> None:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image):
            raise ModelError("model_runtime_missing")
        suffix = {
            "openai-completions": "/chat/completions",
            "openai-responses": "/responses",
            "anthropic-messages": "/messages",
        }.get(self.protocol)
        if not suffix or not self.endpoint.endswith(suffix):
            raise ModelError("model_protocol_unsupported")
        base = self.endpoint[: -len(suffix)]
        conn = providers.connection(
            Connection(provider=self.provider, name="check", base_url=base, protocol=self.protocol)
        )
        if conn["base_url"] != base:
            raise ModelError("model_endpoint_invalid")
        providers.check_effort(self.provider, self.model, self.protocol, self.effort)
        if not self.model or len(self.model) > 200 or any(ord(c) < 33 for c in self.model):
            raise InvalidInput("invalid model")
        if (
            not Path(self.secret_file).is_absolute()
            or self.currency != "USD"
            or self.price_revision != "unknown"
        ):
            raise InvalidInput("invalid registered credential or pricing")
        if self.input_nusd != 0 or self.output_nusd != 0:
            raise InvalidInput("unverified registered pricing")
        for name, ceiling in {
            "max_input_tokens": 200000,
            "max_output_tokens": 32768,
            "max_request_bytes": 262144,
            "max_calls": 100,
            "daily_calls": 1000,
            "timeout_seconds": 600,
            "max_attempts": 1,
            "task_limit_nusd": 10**12,
            "daily_limit_nusd": 10**12,
        }.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= ceiling:
                raise InvalidInput("invalid registered limits")


def resolve(config: Config, identity: str = "app-default") -> RegisteredPolicy:
    with database.connect(config.state_root) as db:
        if identity == "app-default":
            identity = store.default(db)["configuration_id"]
        if not identity:
            raise ModelError("model_unconfigured")
        row = store.get(db, identity)
        credential = db.execute(
            "SELECT * FROM model_credentials WHERE id=?", (row["credential_id"],)
        ).fetchone()
        if not row["active"] or not credential or not credential["active"]:
            raise ModelError("model_disabled")
        data = json.loads(row["settings_json"])
        connection = json.loads(credential["settings_json"])
    from .runtime import expected_image

    protocol = connection["protocol"]
    if connection["provider"] == "opencodego" and data["model"].startswith(("gpt-", "grok-")):
        protocol = "openai-responses"
    suffix = {
        "openai-completions": "/chat/completions",
        "openai-responses": "/responses",
        "anthropic-messages": "/messages",
    }[protocol]
    result = RegisteredPolicy(
        image=expected_image(),
        endpoint=connection["base_url"] + suffix,
        model=data["model"],
        secret_file=credential["secret_file"],
        price_revision="unknown",
        input_nusd=0,
        output_nusd=0,
        max_input_tokens=16384,
        max_output_tokens=4096,
        max_request_bytes=262144,
        task_limit_nusd=10**12,
        daily_limit_nusd=10**12,
        max_calls=8,
        daily_calls=1000,
        max_attempts=1,
        timeout_seconds=120,
        timezone="Asia/Taipei",
        provider=connection["provider"],
        protocol=protocol,
        effort=data["effort"],
        configuration_id=row["id"],
        configuration_revision=row["revision"],
        credential_generation=credential["generation"],
    )
    result.validate()
    return result


def snapshot(config: Config, identity: str = "app-default") -> dict[str, Any]:
    value = resolve(config, identity)
    return {
        **asdict(value),
        "configuration_sha256": value.digest,
        "processing_location": "local" if value.provider == "local" else "cloud",
    }


def has_default(config: Config) -> bool:
    with database.connect(config.state_root) as db:
        return bool(store.default(db)["configuration_id"])
