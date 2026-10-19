"""Durable local draft registration after a complete known-cost runtime transcript."""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import database
from . import document_creation_binding as binding
from . import document_creation_dialogue as dialogue
from . import document_creation_execution as execution
from . import document_creation_runtime_state as runtime
from . import document_creation_tasks as tasks
from .config import Config, IntegrityError, InvalidInput, contained, make_directory
from .document_creation_config import CreationExecutionConfig
from .document_creation_contract import MAX_ARTIFACT_BYTES
from .inputs import canonical
from .objects import digest
from .query_routing_files import read


def _persist(path: Path, content: bytes) -> None:
    make_directory(path.parent)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        try:
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
        except FileExistsError:
            if read(path, MAX_ARTIFACT_BYTES) != content:
                raise IntegrityError("document_draft_existing_bytes_differ") from None
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        if read(path, MAX_ARTIFACT_BYTES) != content:
            raise IntegrityError("document_draft_bytes_changed")
        os.fsync(parent)
    finally:
        os.close(parent)


def register(
    config: Config,
    main: sqlite3.Connection,
    attempt: execution.Attempt,
    policy: CreationExecutionConfig,
    owner: execution.Responsibility,
    task_directory: Path,
) -> dict[str, Any]:
    existing = main.execute(
        "SELECT artifact_json FROM document_creation_artifacts WHERE run_id=?", (attempt.run_id,)
    ).fetchone()
    if existing is not None:
        # Idempotent local reconciliation, not a new execution or authorization to deliver.
        data, metadata = get(config, main, attempt.client_id, attempt.task_id)
        if (
            metadata["configuration_sha256"] != attempt.configuration_sha256
            or digest(data) != metadata["artifact"]["sha256"]
        ):
            raise InvalidInput("document_artifact_binding_invalid")
        bound = binding.get(main, attempt.task_id)
        if (
            bound is None
            or bound["owner_id"] != attempt.owner_id
            or policy.sha256 != attempt.configuration_sha256
            or not isinstance(owner, execution.Responsibility)
            or owner.client_id != attempt.client_id
            or owner.task_id != attempt.task_id
            or owner.active is not True
            or owner.owner_id != attempt.owner_id
        ):
            raise InvalidInput("document_execution_owner_invalid")
        return metadata
    candidate = execution.inspect_output(config, main, attempt, policy, owner, task_directory)
    bound = binding.get(main, attempt.task_id)
    assert bound is not None
    session = main.execute(
        "SELECT * FROM document_creation_sessions WHERE run_id=?", (attempt.run_id,)
    ).fetchone()
    if session is None:
        raise InvalidInput("document_runtime_not_attached")
    records = runtime.rows(main, attempt.run_id)
    calls = main.execute("SELECT * FROM model_calls WHERE run_id=?", (attempt.run_id,)).fetchall()
    metadata = runtime.manifest(bound, session, records, calls)
    _, expected, _ = dialogue.draft(json.loads(records[0]["response_json"]))
    if candidate.content != expected:
        raise InvalidInput("document_output_differs_from_response")
    relative = "document-drafts/" + attempt.run_id + "/" + candidate.filename
    path = contained(config.state_root, relative)
    _persist(path, candidate.content)
    with database.transaction(main):
        execution.verify(config, main, attempt, policy, owner)
        raw = canonical(metadata)
        main.execute(
            "INSERT INTO document_creation_artifacts VALUES (?,1,?,?,?,?)",
            (attempt.run_id, relative, raw, digest(raw.encode()), time.time()),
        )
        main.execute(
            "UPDATE execution_runs SET state='succeeded',error_code=NULL,updated_at=? "
            "WHERE id=? AND state='running'",
            (time.time(), attempt.run_id),
        )
        binding.checked(main, bound)
    return metadata


def get(
    config: Config, main: sqlite3.Connection, client_id: str, task_id: str
) -> tuple[bytes, dict[str, Any]]:
    bound = binding.owned(main, client_id, task_id)
    run, _ = binding.checked(main, bound)
    if run["state"] != "succeeded":
        raise InvalidInput("document_draft_not_ready")
    tasks.plan(config, main, client_id, task_id)
    row = main.execute(
        "SELECT * FROM document_creation_artifacts WHERE run_id=?", (run["id"],)
    ).fetchone()
    assert row is not None
    metadata = json.loads(row["artifact_json"])
    raw = read(contained(config.state_root, row["relative_path"]), MAX_ARTIFACT_BYTES)
    if (
        digest(raw) != metadata["artifact"]["sha256"]
        or len(raw) != metadata["artifact"]["byte_count"]
    ):
        raise IntegrityError("document_draft_bytes_changed")
    tasks.plan(config, main, client_id, task_id)
    return raw, metadata
