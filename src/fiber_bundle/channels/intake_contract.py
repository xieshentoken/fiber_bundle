"""Explicit per-channel archival policy; automatic processing never grants data access."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator

from ..payloads import Identifier, Payload
from .intake_links import domain

Site = Literal["x", "wechat", "bilibili", "youtube", "wechat_channels"]
Material = Literal["text", "link", "image", "file"]
MATERIALS: tuple[Material, ...] = ("text", "link", "image", "file")
DEFAULT_SITES: tuple[Site, ...] = ("x", "wechat", "bilibili", "youtube", "wechat_channels")
SITES = [
    {"id": "x", "name": "X 长文章", "kind": "article"},
    {"id": "wechat", "name": "微信公众号（含图片型）", "kind": "article"},
    {"id": "bilibili", "name": "Bilibili", "kind": "video"},
    {"id": "youtube", "name": "YouTube", "kind": "video"},
    {"id": "wechat_channels", "name": "微信视频号", "kind": "video"},
]


class Settings(Payload):
    policy_version: Literal[1, 2] = 1
    enabled: bool = False
    target_vault_id: Identifier | None = None
    materials: Annotated[list[Material], Field(max_length=4)] = Field(
        default_factory=lambda: list(MATERIALS)
    )
    sites: Annotated[list[Site], Field(max_length=5)] = Field(
        default_factory=lambda: list(DEFAULT_SITES)
    )
    custom_domains: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)
    grouping: Literal["batch", "message"] = "batch"
    store_limit: Annotated[int, Field(ge=0, le=100)] = 0
    download_limit: Annotated[int, Field(ge=0, le=100)] = 0
    outbound_limit: Annotated[int, Field(ge=0, le=100)] = 0
    receipt: bool = False
    fetch_articles: bool = False
    fetch_subtitles: bool = False

    @field_validator("materials", "sites")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate intake selection")
        return value

    @field_validator("custom_domains")
    @classmethod
    def domains(cls, value: list[str]) -> list[str]:
        result = [domain(item) for item in value]
        if len(set(result)) != len(result):
            raise ValueError("duplicate intake domain")
        return result


class Save(Payload):
    expected_revision: Annotated[int, Field(ge=0)]
    settings: Settings


class BatchAction(Payload):
    expected_revision: Annotated[int, Field(ge=1)]
    batch_ids: Annotated[list[Identifier], Field(min_length=1, max_length=20)]


class Revision(Payload):
    expected_revision: Annotated[int, Field(ge=1)]


class Retry(Revision):
    approve_new_model_attempt: StrictBool = False
    video_note_mode: Literal["visual_evidence"] | None = None


class Review(Payload):
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    source_reviewed: Literal[True]
    text_reviewed: Literal[True]
    images_reviewed: Literal[True]

    @field_validator("source_reviewed", "text_reviewed", "images_reviewed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit review confirmation required")
        return value

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review reason required")
        return value


class Publish(Payload):
    review_id: Identifier


class Acknowledgement(Payload):
    acknowledgement: Literal["sent", "not_sent", "unknown"]
