"""Owner-bound public acquisition and explicit browser-copy handoff; no implicit login fallback."""

import base64
import hashlib
import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, media_jobs, media_link_worker, operations, vaults
from .config import Config, IntegrityError, InvalidInput
from .media_files import canonical
from .media_link_artifact import duration
from .media_link_contract import source_url
from .media_link_failure import from_code, stored
from .media_worker import MediaPolicy
from .payloads import Asset, Intake


def access(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM media_link_tasks WHERE id=? AND client_id=?", (identity, cid)
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    vaults.checked_vault(config, auth.grant(db, cid, row["vault_id"], "add"))
    if source_url(row["url"]) != row["url"]:
        raise IntegrityError("media_link_url_changed")
    return row


def create(
    config: Config,
    cid: str,
    vault: str,
    url: str,
    mode: str,
    duration: int,
    image: str,
    *,
    keep_media: bool = False,
) -> str:
    url = source_url(url)
    MediaPolicy(image).validate()
    if (
        mode not in ("audio_only", "full_evidence")
        or type(duration) is not int
        or not 1 <= duration <= 60000
        or type(keep_media) is not bool
    ):
        raise InvalidInput("media_link_request")
    identity = str(uuid4())
    now = time.time()
    with media_jobs.connection(config) as db:
        vaults.checked_vault(config, auth.grant(db, cid, vault, "add"))
        db.execute(
            "INSERT INTO media_link_tasks VALUES(?,?,?,?,?,?,?,?,'queued',NULL,NULL,NULL,?,?)",
            (identity, cid, vault, url, mode, duration, image, int(keep_media), now, now),
        )
    return identity


def summary(row: sqlite3.Row) -> dict[str, Any]:
    failure = from_code(row["error_code"])
    return {
        "link_id": row["id"],
        "state": row["state"],
        "job_id": row["job_id"],
        "error_code": "media_link_attempt_blocked" if failure else row["error_code"],
        "failure_reason": failure,
        "browser_handoff_available": row["state"] == "blocked",
        "login_verified": False,
        "publication_ready": False,
        "cleanup_allowed": False,
    }


def status(config: Config, cid: str, identity: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        return summary(access(config, db, cid, identity))


def import_locked(
    config: Config,
    db: sqlite3.Connection,
    row: sqlite3.Row,
    value: dict[str, Any],
    data: bytes,
    media_image: str,
) -> dict[str, Any]:
    # A fresh linked receipt cannot relabel an existing direct receipt as disposable.
    spec = {k: row[k] for k in ("url", "mode", "declared_duration_ms")}
    duration(value, data, spec, browser=True)
    receipt = operations.intake(
        config,
        db,
        row["client_id"],
        Intake(
            vault_id=row["vault_id"],
            event_id="media-link-" + row["id"],
            source_key=row["url"],
            session_id="media-link-" + row["id"],
            raw_event=base64.b64encode(
                canonical({"url": row["url"], "access_state": value["access_state"]})
            ).decode(),
            assets=[
                Asset(
                    **{
                        "role": "attachment",
                        "media_type": value["media_type"],
                        "original_name": "linked.bin",
                        "data": base64.b64encode(data).decode(),
                    }
                )
            ],
        ),
    )
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt["source_version_id"],)
    ).fetchone()[0]
    # The imported receipt and resulting task remain bound without implicit recovery.
    binding = {
        "receipt_id": receipt["job_id"],
        "asset_id": asset,
        "capture": value,
        "media_image": media_image,
    }
    db.execute(
        "UPDATE media_link_tasks SET state='running',binding_json=?,updated_at=? WHERE id=?",
        (canonical(binding).decode(), time.time(), row["id"]),
    )
    return binding


def finish(config: Config, cid: str, identity: str, binding: dict[str, Any]) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        row = access(config, db, cid, identity)
        if row["state"] != "running" or json.loads(row["binding_json"]) != binding:
            raise InvalidInput("media_link_stopped")
        capture = binding["capture"]
        converted = capture.get("converted_duration_ms", capture["declared_duration_ms"])
        job = media_jobs.create_locked(
            config,
            db,
            cid,
            binding["receipt_id"],
            binding["asset_id"],
            binding["capture"]["mode"],
            converted,
            MediaPolicy(binding["media_image"]),
        )
        db.execute(
            "UPDATE media_link_tasks SET state='complete',job_id=?,updated_at=? WHERE id=?",
            (job, time.time(), identity),
        )
        return summary(access(config, db, cid, identity))


def run(config: Config, cid: str, identity: str, media_image: str) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        row = access(config, db, cid, identity)
        if row["state"] != "queued":
            raise InvalidInput("media_link_no_replay")
        spec = {
            "url": row["url"],
            "mode": row["mode"],
            "declared_duration_ms": row["declared_duration_ms"],
        }
        snapshot = dict(row)
        db.execute(
            "UPDATE media_link_tasks SET state='running',updated_at=? WHERE id=?",
            (time.time(), identity),
        )

    def guard() -> None:
        with media_jobs.connection(config) as db:
            r = access(config, db, cid, identity)
            if r["state"] != "running" or any(
                r[k] != snapshot[k] for k in ("url", "mode", "image", "declared_duration_ms")
            ):
                raise InvalidInput("media_link_stopped")

    try:
        value, data = media_link_worker.execute(config, identity, row["image"], spec, guard=guard)
        with media_jobs.connection(config) as db:
            active = access(config, db, cid, identity)
            if active["state"] != "running":
                raise InvalidInput("media_link_stopped")
            binding = import_locked(config, db, active, value, data, media_image)
        return finish(config, cid, identity, binding)
    except BaseException as error:
        with media_jobs.connection(config) as db:
            db.execute(
                "UPDATE media_link_tasks SET state='blocked',"
                "error_code=?,updated_at=? "
                "WHERE id=? AND state='running'",
                (stored(error), time.time(), identity),
            )
        raise error


def browser_import(
    config: Config,
    cid: str,
    identity: str,
    data: bytes,
    media_image: str,
    *,
    authorized_playback: bool,
) -> dict[str, Any]:
    if (
        authorized_playback is not True
        or not isinstance(data, bytes)
        or not 0 < len(data) <= 8 * 1024 * 1024
    ):
        raise InvalidInput("media_link_browser_authorization_required")
    with media_jobs.connection(config) as db:
        row = access(config, db, cid, identity)
        if row["state"] not in ("queued", "blocked") or row["binding_json"] is not None:
            raise InvalidInput("media_link_no_replay")
        value = {
            "schema": "fiber-media-link-capture/v1",
            "url": row["url"],
            "mode": row["mode"],
            "declared_duration_ms": row["declared_duration_ms"],
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "source_bytes": len(data),
            "media_type": "audio/wav" if row["mode"] == "audio_only" else "video/mp4",
            "access_state": "browser_copy_attested",
            "source_coverage_verified": False,
            "cookies_exported": False,
        }
        binding = import_locked(config, db, row, value, data, media_image)
    return finish(config, cid, identity, binding)


def stop(config: Config, cid: str, identity: str, *, recover: bool = False) -> dict[str, Any]:
    with media_jobs.connection(config) as db:
        row = access(config, db, cid, identity)
        if row["state"] == "complete":
            raise InvalidInput("media_link_terminal")
        db.execute(
            "UPDATE media_link_tasks SET state=?,updated_at=? WHERE id=?",
            ("interrupted" if recover else "cancelled", time.time(), identity),
        )
    if media_link_worker.directory(config, identity).exists():
        media_link_worker.stop_owned(config, row["image"], identity)
    return status(config, cid, identity)
