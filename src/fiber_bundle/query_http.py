"""Strict query transport contracts and current identity checks."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import Request
from pydantic import Field, field_validator

from . import auth, database
from .answer_payloads import NoteSubmission, ScopedSubmission, Submission
from .config import Config
from .payloads import Identifier, Payload

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Role = Literal["local", "cloud"]


class LocalSubmission(Submission):
    configuration_sha256: Digest


class LocalNoteSubmission(NoteSubmission):
    configuration_sha256: Digest


class LocalScopedSubmission(ScopedSubmission):
    configuration_sha256: Digest


class CloudSubmission(Payload):
    local_task_id: Identifier
    request_id: Identifier
    configuration_sha256: Digest


class JobSubmission(Payload):
    role: Role
    task_id: Identifier
    cloud_edit: bool = False


class OwnedJob(JobSubmission):
    client_id: Identifier


class BackupSelection(Payload):
    client_id: Identifier
    backup_index: Annotated[int, Field(ge=1, le=4)]
    selection_id: Annotated[str, Field(min_length=1, max_length=128)]
    approve: Literal[True]

    @field_validator("approve", mode="before")
    @classmethod
    def approved(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit_boolean_approval_required")
        return value


class PolicySave(Payload):
    expected_sha256: Digest | None
    value: dict[str, object]


@contextmanager
def access(
    config: Config, request: Request, *, admin: bool = False
) -> Iterator[tuple[sqlite3.Connection, str | None]]:
    from .display_observation import current as display_read

    db = database.connect(config.state_root, readonly=display_read.get() is not None)
    try:
        if admin:
            auth.admin(
                db,
                request.cookies.get(auth.COOKIE, ""),
                request.headers.get("x-csrf-token", "")
                if request.method not in {"GET", "HEAD"}
                else None,
            )
            yield db, None
        else:
            yield db, auth.client(db, request.headers.get("authorization", ""))["id"]
    finally:
        db.close()


def active(db: sqlite3.Connection, client_id: str) -> None:
    row = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if row is None or not row[0]:
        raise auth.Denied(401)
