"""Typed contracts for directory discovery and draft per-Vault save settings."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from .config import InvalidInput
from .payloads import Identifier, Payload
from .vault_scope import prefix


class SettingsError(InvalidInput):
    def __init__(self, code: str, status: int = 422):
        # All call sites use constant codes, never source text or received paths.
        if not code.startswith(("save_policy_", "vault_directories_")) or status not in {409, 422}:
            raise ValueError("invalid settings error")
        self.status = status
        super().__init__(code)


def directory(value: str) -> str:
    try:
        normalized = prefix(value)
    except InvalidInput as exc:
        raise ValueError("invalid save directory") from exc
    if (
        not normalized
        or normalized != value
        or len(value.encode()) > 1024
        or any(
            p.startswith(".") or p.casefold() in {"trash", "$recycle.bin"} or p.endswith(".icloud")
            for p in value.split("/")
        )
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("invalid save directory")
    return normalized


class SaveSettings(Payload):
    policy_version: Literal[1, 2] = 1
    layout_mode: Literal["legacy_bundle", "obsidian_folders"] = "legacy_bundle"
    inbox_dir: str = "Inbox"
    archive_dir: str = "Archive"
    image_dir: str = "images"
    image_link_style: Literal["obsidian", "markdown"] = "obsidian"
    archive_on_moc: bool = False
    moc_destinations: Annotated[dict[str, str], Field(max_length=100)] = {}
    moc_dir: str = "Map"
    book_dir: str = "Book"
    attachment_root: str = "Attachments"
    original_dir: str = "Attachments/pristin"
    moc_archive_mode: Literal["off", "preview", "automatic"] = "off"

    @model_validator(mode="after")
    def explicit_version(self) -> "SaveSettings":
        if self.policy_version == 1 and self.model_fields_set & VERSION_TWO_FIELDS:
            raise ValueError("new directory roles require policy_version=2")
        if self.policy_version == 2 and self.archive_on_moc:
            raise ValueError("version two requires explicit moc_archive_mode")
        return self

    @field_validator(
        "inbox_dir",
        "archive_dir",
        "image_dir",
        "moc_dir",
        "book_dir",
        "attachment_root",
        "original_dir",
    )
    @classmethod
    def valid_directory(cls, value: str) -> str:
        return directory(value)

    @field_validator("moc_destinations")
    @classmethod
    def valid_destinations(cls, value: dict[str, str]) -> dict[str, str]:
        from uuid import UUID

        for identity, path in value.items():
            if str(UUID(identity)) != identity:
                raise ValueError("invalid MOC identity")
            directory(path)
        return value


VERSION_TWO_FIELDS = {"moc_dir", "book_dir", "attachment_root", "original_dir", "moc_archive_mode"}


def settings_wire(settings: SaveSettings) -> dict[str, object]:
    """Keep old frozen JSON byte-equivalent; never inject version-two defaults."""
    return settings.model_dump(
        exclude=VERSION_TWO_FIELDS | {"policy_version"} if settings.policy_version == 1 else set()
    )


def unified_settings() -> SaveSettings:
    """An explicit UI preset; callers must still preview and enable it."""
    return SaveSettings(
        policy_version=2,
        layout_mode="obsidian_folders",
        archive_dir="Resource",
        image_dir="Attachments/images",
    )


def archive_mode(settings: dict[str, object]) -> str:
    return (
        str(settings.get("moc_archive_mode", "off"))
        if settings.get("policy_version") == 2
        else "preview"
        if settings.get("archive_on_moc")
        else "off"
    )


class DirectoryScope(Payload):
    include: Annotated[list[str], Field(min_length=1, max_length=100)] = [""]
    exclude: Annotated[list[str], Field(max_length=100)] = []


class Discovery(Payload):
    root: Annotated[str | None, Field(max_length=4096)] = None
    scope: DirectoryScope = DirectoryScope()
    directory_preview_id: Identifier | None = None
    relative_parent: Annotated[str, Field(max_length=1024)] = ""
    cursor: Annotated[str | None, Field(max_length=100)] = None


class Preview(Payload):
    expected_revision: Annotated[int, Field(ge=0)]
    settings: SaveSettings
    enable_publication: StrictBool = False


class Save(Payload):
    preview_id: Identifier
    expected_revision: Annotated[int, Field(ge=0)]


class Abandon(Payload):
    operation_id: Identifier
    preserve_directories: Literal[True]

    @field_validator("preserve_directories", mode="before")
    @classmethod
    def explicit_preservation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit directory preservation required")
        return value
