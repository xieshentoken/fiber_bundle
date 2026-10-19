"""Public model inputs. Secret values are excluded from representations."""

from typing import Literal

from pydantic import Field, field_validator

from ..payloads import Payload

Provider = Literal["openai", "anthropic", "deepseek", "opencodego", "custom", "local"]
Protocol = Literal["openai-completions", "openai-responses", "anthropic-messages"]


class Connection(Payload):
    provider: Provider
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=2000)
    protocol: Protocol = "openai-completions"
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    credential_id: str | None = Field(default=None, pattern=r"^key_[a-f0-9]{64}$")
    credential_revision: int = Field(default=0, ge=0)

    @field_validator("api_key")
    @classmethod
    def key_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError("model_key_format")
        return value


class Save(Connection):
    model: str = Field(min_length=1, max_length=200)
    effort: str = Field(default="default", max_length=20)
    configuration_id: str | None = Field(default=None, pattern=r"^model_[a-f0-9]{32}$")
    expected_revision: int = Field(default=0, ge=0)
    budget_tokens: int = Field(default=100000, ge=0, le=10**12)

    @field_validator("model")
    @classmethod
    def model_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 33 for c in value):
            raise ValueError("model_id_format")
        return value


class Change(Payload):
    expected_revision: int = Field(ge=0)
    action: Literal["default", "enable", "disable", "delete"]
    expected_default_revision: int | None = Field(default=None, ge=0)


class Test(Payload):
    expected_revision: int = Field(ge=1)
    approve_tokens: bool = False
