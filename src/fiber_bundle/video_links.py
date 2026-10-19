"""Explicit public-link acquisition into the existing import archive, then the video pipeline."""

import base64
import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import (
    automatic_policy,
    database,
    source_imports,
    source_processing_policy,
    video_contract,
    video_runner,
    video_tasks,
    video_tools,
)
from .config import Config, InvalidInput, contained, make_directory
from .executor_container import run_lock
from .inputs import canonical
from .media_link_contract import source_url
from .objects import digest
from .payloads import Identifier
from .query_policy_store import write_private
from .query_routing_files import read


class Create(video_contract.Create):
    # A link has no pre-existing import; the archive is created only after acquisition.
    import_id: Identifier = "link"
    vault_id: Identifier
    url: str


def get(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND kind='video_link'", (identity,)
    ).fetchone()
    if row is None:
        raise InvalidInput("video_link_unavailable")
    value = json.loads(row["payload"])
    if value["request"]["client_id"] != cid:
        from .auth import Denied

        raise Denied(404)
    from . import auth, vaults

    for permission in ("add", "original", "query"):
        vaults.checked_vault(config, auth.grant(db, cid, row["vault_id"], permission))
    if (
        source_imports.epoch(db, cid, row["vault_id"]) != value["epoch"]
        or automatic_policy.get(db, row["vault_id"]) != value["policy"]
        or video_tools.code_digest() != value["tool_sha256"]
    ):
        raise InvalidInput("video_processing_policy_changed")
    return {**dict(row), "value": value}


def wire(row: dict[str, Any]) -> dict[str, Any]:
    from .processing_errors import MESSAGES

    return {
        "error_message": MESSAGES.get(row["value"].get("error_code")),
        "id": row["id"],
        "state": row["state"],
        "kind": "link_acquisition",
        "client_id": row["value"]["request"]["client_id"],
        "vault_id": row["vault_id"],
        "video_id": row["value"].get("video_id"),
        "reason": row["value"].get("error_code"),
    }


def update(db: sqlite3.Connection, row: dict[str, Any], state: str) -> None:
    db.execute(
        "UPDATE vault_operations SET state=?,payload=?,updated_at=? WHERE id=?",
        (state, canonical(row["value"]), time.time(), row["id"]),
    )


def create(config: Config, db: sqlite3.Connection, data: Create) -> dict[str, Any]:
    request = data.model_dump()
    request["url"] = source_url(data.url)
    resources = video_tasks.settings(config)["settings"]
    if not resources["link_image"] or not resources["image"]:
        raise InvalidInput("video_link_runtime_not_configured")
    if not data.subtitle and not resources["asr_resources"]:
        raise InvalidInput("video_transcription_capability_missing")
    if not data.subtitle and data.glossary is not None:
        video_contract.confirmed_glossary(data.glossary, data.glossary_confirmed)
    from . import auth, vaults

    for permission in ("add", "original", "query"):
        vaults.checked_vault(config, auth.grant(db, data.client_id, data.vault_id, permission))
    identity, now = str(uuid4()), time.time()
    value = {
        "request": request,
        "resources": resources,
        "policy": automatic_policy.get(db, data.vault_id),
        "epoch": source_imports.epoch(db, data.client_id, data.vault_id),
        "tool_sha256": video_tools.code_digest(),
        "source_policy": source_processing_policy.inspect(config, None)["configuration_sha256"],
    }
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,'video_link','queued',?,?,?)",
        (identity, data.vault_id, canonical(value), now, now),
    )
    return wire(get(config, db, data.client_id, identity))


def run(config: Config, cid: str, identity: str) -> None:
    def guard() -> None:
        with database.locked(config), database.connect(config.state_root) as db:
            if get(config, db, cid, identity)["state"] not in {"queued", "running"}:
                raise InvalidInput("video_task_stopped")

    with run_lock(config, identity):
        try:
            with database.locked(config), database.connect(config.state_root) as db:
                row = get(config, db, cid, identity)
                if row["state"] not in {"queued", "running"}:
                    return
                update(db, row, "running")
            value = row["value"]
            request = value["request"]
            if not value.get("import_id"):
                attempt = str(uuid4())
                root = contained(config.state_root, "video-links/" + attempt)
                make_directory(root / "input")
                make_directory(root / "output")
                spec = {
                    "url": request["url"],
                    "mode": "full_evidence",
                    "declared_duration_ms": request["duration_ms"],
                }
                write_private(root / "input/request.json", canonical(spec).encode())
                video_tools.container(
                    root,
                    attempt,
                    value["resources"]["link_image"],
                    "video_link_entry",
                    900,
                    guard,
                    network="bridge",
                )
                raw = read(root / "output/source", video_contract.MAX_SOURCE_BYTES)
                result = json.loads(read(root / "output/result.json", 4096))
                expected = {
                    "schema": "fiber-media-link-capture/v3",
                    **spec,
                    "converted_duration_ms": result.get("converted_duration_ms"),
                    "source_sha256": digest(raw),
                    "source_bytes": len(raw),
                    "media_type": "video/mp4",
                    "access_state": "public_acquired",
                    "source_coverage_verified": False,
                    "cookies_exported": False,
                }
                if (
                    result != expected
                    or type(result["converted_duration_ms"]) is not int
                    or abs(result["converted_duration_ms"] - request["duration_ms"]) > 100
                ):
                    raise InvalidInput("video_source_duration_mismatch")
                guard()
                with database.locked(config), database.connect(config.state_root) as db:
                    if (
                        source_processing_policy.inspect(config, None)["configuration_sha256"]
                        != value["source_policy"]
                    ):
                        raise InvalidInput("video_processing_policy_changed")
                    imported = source_imports.create(
                        config,
                        db,
                        {
                            "client_id": cid,
                            "vault_id": row["vault_id"],
                            "submission_id": identity,
                            "filename": "linked-video.mp4",
                            "source_sha256": digest(raw),
                            "source_bytes": len(raw),
                            "action": "archive",
                            "engine": "ordinary",
                            "profile": "native",
                            "configuration_sha256": value["source_policy"],
                            "overrides": {},
                            "references": {},
                        },
                    )
                    # Persist import identity before writing chunks; a resume adopts the same bytes.
                    value["import_id"] = imported["id"]
                    value["capture_path"] = str(root.relative_to(config.state_root))
                    update(db, row, "running")
            guard()
            with database.locked(config), database.connect(config.state_root) as db:
                archived = source_imports.get(config, db, cid, value["import_id"])
                if archived["state"] != "archived":
                    raw = read(
                        contained(config.state_root, value["capture_path"] + "/output/source"),
                        video_contract.MAX_SOURCE_BYTES,
                    )
                    for ordinal, offset in enumerate(
                        range(0, len(raw), source_imports.CHUNK_BYTES)
                    ):
                        chunk = raw[offset : offset + source_imports.CHUNK_BYTES]
                        source_imports.chunk(
                            config,
                            db,
                            cid,
                            value["import_id"],
                            ordinal,
                            digest(chunk),
                            base64.b64encode(chunk).decode(),
                        )
                    source_imports.finish(config, db, cid, value["import_id"])
                params = {k: v for k, v in request.items() if k not in {"url", "vault_id"}}
                params["import_id"] = value["import_id"]
                task = video_tasks.create(config, db, video_contract.Create.model_validate(params))
                value["video_id"] = task["id"]
                update(db, row, "complete")
            video_runner.run(config, cid, task["id"])
        except Exception as error:
            with database.locked(config), database.connect(config.state_root) as db:
                saved = db.execute(
                    "SELECT * FROM vault_operations WHERE id=?", (identity,)
                ).fetchone()
                if saved and saved["state"] != "cancelled":
                    row = {**dict(saved), "value": json.loads(saved["payload"])}
                    from .processing_errors import MESSAGES

                    row["value"]["error_code"] = (
                        str(error) if str(error) in MESSAGES else "video_processing_failed"
                    )
                    update(db, row, "failed")
            raise
