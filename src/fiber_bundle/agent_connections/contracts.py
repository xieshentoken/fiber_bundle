"""Versioned connection and turn contracts. No credentials, paths or execution here."""

import hashlib
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from ..inputs import canonical, text
from ..payloads import Identifier, Payload
from ..search_matching import terms

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
AnswerPolicy = Literal["evidence_only", "augmented"]


def identifiers(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("duplicate_identifier")
    for item in value:
        text(item, limit=400)
    return value


class ConnectionLimits(Payload):
    queries: Annotated[int, Field(ge=1, le=2)] = 2
    results_per_query: Annotated[int, Field(ge=1, le=5)] = 5
    evidence_chars: Annotated[int, Field(ge=400, le=16000)] = 8000
    prompt_bytes: Annotated[int, Field(ge=4096, le=65536)] = 32768
    timeout_seconds: Annotated[int, Field(ge=1, le=180)] = 60
    provider_calls: Annotated[int, Field(ge=1, le=1)] = 1
    output_tokens: Annotated[int, Field(ge=128, le=4096)] = 1024


class ConnectionSpec(Payload):
    schema_version: Literal["fiber-agent-connection/v1", "fiber-agent-connection/v2"] = (
        "fiber-agent-connection/v1"
    )
    name: Annotated[str, Field(min_length=1, max_length=100)]
    agent_type: Literal["hermes", "mcp"]
    vault_ids: Annotated[list[Identifier], Field(min_length=1, max_length=20)]
    answer_policy: AnswerPolicy = "augmented"
    quality_scope: Literal["all", "complete"] = "all"
    allow_originals: bool = False
    limits: ConnectionLimits = Field(default_factory=ConnectionLimits)
    model_configuration_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")] | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return text(value, limit=100)

    @field_validator("vault_ids")
    @classmethod
    def valid_vaults(cls, value: list[str]) -> list[str]:
        return identifiers(value)

    @model_validator(mode="after")
    def model_owner(self) -> Self:
        if self.schema_version.endswith("/v1") and (
            self.quality_scope != "all" or self.allow_originals
        ):
            raise ValueError("agent_quality_requires_version_two")
        if self.agent_type == "mcp" and self.model_configuration_id is not None:
            raise ValueError("mcp_model_belongs_to_external_host")
        return self

    @model_serializer(mode="wrap")
    def wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value = dict(handler(self))
        if self.schema_version.endswith("/v1"):
            value.pop("quality_scope", None)
        if not self.allow_originals:
            value.pop("allow_originals", None)
        return value

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump()).encode()).hexdigest()


class ConnectionContext(Payload):
    """Trusted host projection; authorization SHA must bind permission epochs, not just booleans."""

    connection_id: Identifier
    client_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    credential_generation: Annotated[int, Field(ge=1)]
    authorization_sha256: Digest
    configuration_sha256: Digest
    enabled: bool
    configuration: ConnectionSpec

    @field_validator("connection_id", "client_id")
    @classmethod
    def valid_identity(cls, value: str) -> str:
        return text(value, limit=400)

    @model_validator(mode="after")
    def bound_configuration(self) -> Self:
        if self.configuration_sha256 != self.configuration.digest:
            raise ValueError("configuration_digest_mismatch")
        return self


class TurnRequest(Payload):
    request_id: Identifier
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    retrieval_queries: Annotated[list[str], Field(min_length=1, max_length=2)]
    vault_ids: Annotated[list[Identifier], Field(min_length=1, max_length=20)] | None = None

    @field_validator("request_id", "question")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return text(value, limit=2000)

    @field_validator("vault_ids")
    @classmethod
    def valid_vaults(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else identifiers(value)

    @field_validator("retrieval_queries")
    @classmethod
    def valid_queries(cls, value: list[str]) -> list[str]:
        identifiers(value)
        for query in value:
            terms(query)
        return value


class Claim(Payload):
    text: Annotated[str, Field(min_length=1, max_length=600)]
    citations: Annotated[list[str], Field(min_length=1, max_length=10)]

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return text(value, limit=600)


class AnswerProposal(Payload):
    schema_version: Literal["fiber-agent-answer/v1"]
    claims: Annotated[list[Claim], Field(max_length=8)]
    model_supplement: Annotated[str, Field(min_length=1, max_length=2000)] | None

    @field_validator("model_supplement")
    @classmethod
    def valid_supplement(cls, value: str | None) -> str | None:
        return None if value is None else text(value, limit=2000)
