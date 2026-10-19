"""Strict GUI policy contract. Defaults are suggestions, never an enabled schedule."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from ..payloads import Payload


class Policy(Payload):
    enabled: bool = False
    directory: Annotated[str, Field(max_length=4096)] = ""
    interval_minutes: Annotated[int, Field(strict=True, ge=0, le=525600)] = 0
    daily_time: str = ""
    on_change: bool = False
    debounce_seconds: Annotated[int, Field(strict=True, ge=30, le=86400)] = 300
    growth_mb: Annotated[int, Field(strict=True, ge=0, le=1048576)] = 0
    after_moc: bool = False
    keep_recent: Annotated[int, Field(strict=True, ge=1, le=1000)] = 5
    keep_days: Annotated[int, Field(strict=True, ge=0, le=3660)] = 7
    keep_weeks: Annotated[int, Field(strict=True, ge=0, le=520)] = 4
    max_total_mb: Annotated[int, Field(strict=True, ge=0, le=10485760)] = 0
    max_file_mb: Annotated[int, Field(strict=True, ge=1, le=1048576)] = 256
    max_snapshot_mb: Annotated[int, Field(strict=True, ge=1, le=10485760)] = 2048
    max_entries: Annotated[int, Field(strict=True, ge=100, le=1000000)] = 20000

    @field_validator("daily_time")
    @classmethod
    def time_format(cls, value: str) -> str:
        if value:
            import datetime

            if len(value) != 5:
                raise ValueError("HH:MM required")
            datetime.time.fromisoformat(value)
        return value


class Save(Payload):
    storage_mode: Literal["incremental", "complete"] | None = None
    full_verify_days: Annotated[int, Field(strict=True, ge=1, le=366)] | None = None
    revision: Annotated[int, Field(strict=True, ge=0)]
    policy: Policy
    recovery_key: Annotated[str, Field(pattern=r"^([a-f0-9]{64})?$")] = ""
    key_saved: bool = False
