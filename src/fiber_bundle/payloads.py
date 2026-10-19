"""Strict HTTP payloads. Byte decoding reuses M1 asset validation."""

import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from fiber_bundle.config import InvalidInput
from fiber_bundle.inputs import AssetInput

Text = Annotated[str, Field(min_length=1, max_length=100)]
Identifier = Annotated[str, Field(min_length=1, max_length=400)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Payload):
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class Named(Payload):
    name: Text


class NewClient(Named):
    create_vault: bool


class Permissions(Payload):
    query: bool
    original: bool
    add: bool


class Approval(Permissions):
    client_id: Identifier


class DefaultQuery(Payload):
    default_query: bool


def decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidInput("invalid base64 bytes") from exc


class Asset(Payload):
    data: str | None
    original_name: Identifier
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    role: Literal["body", "attachment"]
    expected_sha256: str | None = None
    expected_size: Annotated[int, Field(ge=0)] | None = None

    def storage_input(self) -> AssetInput:
        asset = AssetInput(
            None if self.data is None else decode(self.data),
            self.original_name,
            self.media_type,
            self.role,
            self.expected_sha256,
            self.expected_size,
        )
        asset.descriptor()
        return asset


class Intake(Payload):
    vault_id: Identifier
    event_id: Identifier
    source_key: Identifier
    session_id: Identifier
    raw_event: str
    assets: Annotated[list[Asset], Field(min_length=1, max_length=100)]
    retention: Literal["session", "knowledge"] = "session"
