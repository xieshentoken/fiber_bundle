"""Versioned classification policy; scheduling batches never impose a processing quota."""

from typing import Any, Literal

from pydantic import Field, field_validator

from ..config import InvalidInput
from ..payloads import Payload
from ..vault_scope import prefix

ORGANIZER_VERSION = "af674b8ba2ac88ee523010591db4c9f88f9a31ec:fiber-v2-body"


class Condition(Payload):
    field: Literal["path", "kind", "source", "tag", "status", "title", "body", "tag_prefix"]
    values: list[str] = Field(min_length=1, max_length=100)


class Rule(Payload):
    moc_id: str = Field(min_length=1, max_length=100)
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1, max_length=100)


class Policy(Payload):
    mode: Literal["off", "suggest", "automatic"] = "off"
    include: list[str] = Field(default_factory=lambda: [""])
    exclude: list[str] = Field(default_factory=list)
    kinds: list[Literal["note", "expert"]] = ["note", "expert"]
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    events: list[Literal["saved", "changed", "package"]] = ["saved", "changed", "package"]
    interval_seconds: int = Field(default=300, strict=True, ge=30, le=2592000)
    periodic: bool = False
    full_review: bool = False
    minimum_pending: int = Field(default=1, strict=True, ge=1)

    @field_validator("include", "exclude")
    @classmethod
    def paths(cls, value: Any) -> Any:
        if len(value) > 100 or not value:
            if value == []:
                return value
            raise ValueError("organization_scope_invalid")
        try:
            return sorted({prefix(p) for p in value})
        except InvalidInput as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("include")
    @classmethod
    def nonempty(cls, value: Any) -> Any:
        if not value:
            raise ValueError("organization_scope_empty")
        return value


class Command(Payload):
    action: Literal[
        "run", "preview", "confirm", "status", "cancel", "resume", "link", "unlink", "unlock"
    ]
    target: str = "Inbox"
    moc: str | None = None


STATES = {
    "queued",
    "running",
    "waiting_save",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "preview",
}
