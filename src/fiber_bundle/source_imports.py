"""Authorized resumable GUI imports. Caller holds the main storage lease.

Originals use Store; this ledger records an explicit operation, never a platform event.
Operations use the frozen source limit and bounded upload chunks.
"""

import base64
import json
import os
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, database, object_streams, objects, source_processing_policy, vaults
from . import source_processing_contract as contract
from .config import Config, InvalidInput, contained, make_directory, sync_directory
from .inputs import AssetInput, canonical
from .store import Store

CHUNK_BYTES = object_streams.CHUNK_BYTES
FORMATS = {
    ".mp4": ("video", "video/mp4"),
    ".wav": ("audio", "audio/wav"),
    ".md": ("markdown", "text/markdown"),
    ".markdown": ("markdown", "text/markdown"),
    ".txt": ("text", "text/plain"),
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".doc": ("doc", "application/msword"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
    ".bmp": ("image", "image/bmp"),
    ".tif": ("image", "image/tiff"),
    ".tiff": ("image", "image/tiff"),
    ".gif": ("image", "image/gif"),
    ".heic": ("image", "image/heic"),
    ".heif": ("image", "image/heif"),
    ".zip": ("archive", "application/zip"),
    ".xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".pptx": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}


def epoch(db: sqlite3.Connection, client_id: str, vault_id: str) -> int:
    row = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client_id, vault_id),
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    return int(row[0])


def authorize(config: Config, db: sqlite3.Connection, row: sqlite3.Row) -> None:
    for permission in ("add", "original"):
        vaults.checked_vault(config, auth.grant(db, row["client_id"], row["vault_id"], permission))
    if epoch(db, row["client_id"], row["vault_id"]) != row["permission_epoch"] or row["state"] in {
        "cancelled",
        "interrupted",
    }:
        raise auth.Denied(404)
    contract.validate_plan(json.loads(row["plan_json"]))
    from .source_import_references import validate

    validate(
        config,
        db,
        row["client_id"],
        row["vault_id"],
        json.loads(row["request_json"]).get("references", {}),
    )


def get(config: Config, db: sqlite3.Connection, client_id: str, import_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM source_imports WHERE id=? AND client_id=?", (import_id, client_id)
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    authorize(config, db, row)
    return row


def projection(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    from .attachment_projection import receipt_status

    request = json.loads(row["request_json"])
    state = row["state"]
    run_state = None
    publication_pause = None
    if row["run_id"]:
        run_state = db.execute(
            "SELECT state FROM execution_runs WHERE id=?", (row["run_id"],)
        ).fetchone()[0]
        published = db.execute(
            "SELECT state FROM document_publications WHERE run_id=?", (row["run_id"],)
        ).fetchone()
        state = (
            "published"
            if published and published[0] == "published"
            else {"queued": "prepared", "running": "processing", "succeeded": "needs_review"}.get(
                run_state, run_state
            )
        )
    if run_state == "succeeded" and state != "published":
        task = db.execute(
            "SELECT error_code FROM document_tasks WHERE run_id=?", (row["run_id"],)
        ).fetchone()
        from .document_split import CODES

        if task and task[0] in CODES:
            state = "needs_split"
            publication_pause = task[0]
        if task and task[0] == "markdown_references_missing":
            state = "needs_supplement"
    from .expert_tasks import projection as expert_projection

    expert = expert_projection(db, row["id"])
    if expert is not None:
        state = expert["state"]
    return dict(
        id=row["id"],
        client_id=row["client_id"],
        vault_id=row["vault_id"],
        filename=request["filename"],
        source_sha256=request["source_sha256"],
        source_bytes=request["source_bytes"],
        received_bytes=row["received_bytes"],
        state=state,
        run_state=run_state,
        publication_pause=publication_pause,
        run_id=row["run_id"],
        receipt_id=row["receipt_id"],
        asset_id=row["asset_id"],
        plan=json.loads(row["plan_json"]),
        chunk_bytes=CHUNK_BYTES,
        model_calls_allowed=0,
        index_state="not_checked" if state == "published" else "not_published",
        expert=expert,
        original_projections=receipt_status(db, row["receipt_id"]),
        automatic=next(
            (
                dict(j)
                for j in db.execute(
                    "SELECT id,state,phase,reason,updated_at FROM automatic_jobs WHERE"
                    " kind IN ('document','expert') AND source_id=?",
                    (row["id"],),
                )
            ),
            None,
        ),
    )


def listing(
    config: Config, db: sqlite3.Connection, client_id: str, vault_id: str
) -> list[dict[str, Any]]:
    for permission in ("add", "original"):
        vaults.checked_vault(config, auth.grant(db, client_id, vault_id, permission))
    rows = db.execute(
        "SELECT * FROM source_imports WHERE client_id=? AND vault_id=? ORD"
        "ER BY created_at DESC LIMIT 50",
        (client_id, vault_id),
    ).fetchall()
    return [
        projection(db, r) for r in rows if r["permission_epoch"] == epoch(db, client_id, vault_id)
    ]


def create(config: Config, db: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    old = db.execute(
        "SELECT * FROM source_imports WHERE client_id=? AND submission_id=?",
        (request["client_id"], request["submission_id"]),
    ).fetchone()
    wire = canonical(request)
    if old:
        authorize(config, db, old)
        if wire != old["request_json"]:
            raise InvalidInput("import submission conflict")
        if old["state"] == "uploading":
            make_directory(contained(config.state_root, "source-imports/" + old["id"]))
        return projection(db, old)
    descriptor = AssetInput(
        None,
        request["filename"],
        "application/octet-stream",
        expected_sha256=request["source_sha256"],
        expected_size=request["source_bytes"],
    ).descriptor()
    from pathlib import PurePosixPath

    format, mime = FORMATS.get(
        PurePosixPath(descriptor["original_name"]).suffix.lower(),
        ("unknown", "application/octet-stream"),
    )
    settings = source_processing_policy.inspect(config, None)
    if request["engine"] == "fake-expert":
        from .expert_runtime import selection

        selection(request.get("expert_profile", "native-text"), request.get("expert_backend"))
        if request.get("profile", "native") != "native":
            raise InvalidInput("expert_use_explicit_expert_profile")
    if request["configuration_sha256"] != settings["configuration_sha256"]:
        raise InvalidInput("source processing configuration changed")
    policy = settings["value"] or contract.defaults()
    project_skill = None
    if (
        request["engine"] == "fake-expert"
        and request["action"] == "process"
        and request.get("expert_profile", "native-text") == "native-text"
    ):
        from . import project_skills

        project_skill = project_skills.resolve(config)
        if project_skill:
            policy = project_skills.apply_policy(policy, project_skill)
    plan = contract.freeze(
        policy,
        format=format,
        source_sha256=request["source_sha256"],
        source_bytes=request["source_bytes"],
        action=request["action"],
        engine=request["engine"],
        profile=request.get("profile", "native"),
        overrides=request["overrides"],
        project_skill=project_skill,
    )
    if request["action"] == "process" and (
        format not in {"markdown", "text", "pdf", "docx", "xlsx", "pptx", "doc", "image", "archive"}
        or not contract.capability(plan)["binding_ready"]
    ):
        raise InvalidInput("import processing capability unavailable")
    for permission in ("add", "original"):
        vaults.checked_vault(
            config, auth.grant(db, request["client_id"], request["vault_id"], permission)
        )
    from .source_import_references import validate

    references = validate(
        config, db, request["client_id"], request["vault_id"], request.get("references", {})
    )
    if (
        request["source_bytes"] + sum(r["byte_count"] for r in references.values())
        > plan["limits"]["batch_bytes"]
    ):
        raise InvalidInput("import batch limit")
    identity = str(uuid4())
    db.execute(
        "INSERT INTO source_imports(id,client_id,vault_id,submission_id,re"
        "quest_json,plan_json,permission_epoch,state,created_at) VALUES(?,"
        "?,?,?,?,?,?,'uploading',?)",
        (
            identity,
            request["client_id"],
            request["vault_id"],
            request["submission_id"],
            wire,
            canonical(plan),
            epoch(db, request["client_id"], request["vault_id"]),
            time.time(),
        ),
    )
    root = contained(config.state_root, "source-imports/" + identity)
    make_directory(root)
    return projection(db, get(config, db, request["client_id"], identity))


def chunk(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    import_id: str,
    ordinal: int,
    sha256: str,
    encoded: str,
) -> dict[str, Any]:
    row = get(config, db, client_id, import_id)
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise InvalidInput("invalid import chunk encoding") from None
    if not 0 < len(data) <= CHUNK_BYTES or objects.digest(data) != sha256:
        raise InvalidInput("invalid import chunk digest or size")
    path = contained(config.state_root, f"source-imports/{import_id}/{ordinal}.part")
    prior = db.execute(
        "SELECT sha256,byte_count FROM source_import_chunks WHERE import_id=? AND ordinal=?",
        (import_id, ordinal),
    ).fetchone()
    if prior:
        if tuple(prior) != (sha256, len(data)):
            raise InvalidInput("import chunk conflict")
        object_streams.verify(path, sha256, len(data))
        return projection(db, row)
    plan = json.loads(row["plan_json"])
    count = db.execute(
        "SELECT count(*) FROM source_import_chunks WHERE import_id=?", (import_id,)
    ).fetchone()[0]
    expected = min(CHUNK_BYTES, plan["source_bytes"] - row["received_bytes"])
    if row["state"] != "uploading" or ordinal != count or len(data) != expected:
        raise InvalidInput("import chunk order or cumulative size")
    if path.exists():
        # Crash after fsync, before ledger commit: accept only the same bytes.
        object_streams.verify(path, sha256, len(data))
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        sync_directory(path.parent)
    with database.transaction(db):
        db.execute(
            "INSERT INTO source_import_chunks VALUES(?,?,?,?)",
            (import_id, ordinal, sha256, len(data)),
        )
        db.execute(
            "UPDATE source_imports SET received_bytes=received_bytes+? WHERE id=?",
            (len(data), import_id),
        )
    return projection(db, get(config, db, client_id, import_id))


def finish(
    config: Config, db: sqlite3.Connection, client_id: str, import_id: str
) -> dict[str, Any]:
    row = get(config, db, client_id, import_id)
    if row["state"] == "archived":
        Store(config).verify_receipt_locked(db, row["receipt_id"])
        return projection(db, row)
    plan = json.loads(row["plan_json"])
    if row["received_bytes"] != plan["source_bytes"]:
        raise InvalidInput("import upload incomplete")
    chunks = db.execute(
        "SELECT * FROM source_import_chunks WHERE import_id=? ORDER BY ordinal", (import_id,)
    ).fetchall()
    object_streams.assemble(
        config.state_root,
        row["vault_id"],
        (
            (
                contained(config.state_root, f"source-imports/{import_id}/{c['ordinal']}.part"),
                c["sha256"],
                c["byte_count"],
            )
            for c in chunks
        ),
        plan["source_sha256"],
        plan["source_bytes"],
    )
    request = json.loads(row["request_json"])
    vault = auth.grant(db, client_id, row["vault_id"], "add")
    from pathlib import PurePosixPath

    _, mime = FORMATS.get(
        PurePosixPath(request["filename"]).suffix.lower(), ("unknown", "application/octet-stream")
    )
    identity = "gui-import:" + import_id
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope="client:" + client_id,
        event_id=identity,
        vault=vault["name"],
        source_key=identity,
        raw_event=canonical(dict(ingress="gui_import", import_id=import_id)).encode(),
        items=(
            AssetInput(
                None, request["filename"], mime, "body", plan["source_sha256"], plan["source_bytes"]
            ),
        ),
        session_scope="client:" + client_id,
        session_id=identity,
        retention="knowledge",
        owner_client_id=client_id,
    )
    if receipt.state != "stored" or receipt.source_version_id is None:
        raise InvalidInput("import original not stored")
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=? AND role='body'",
        (receipt.source_version_id,),
    ).fetchone()[0]
    db.execute(
        "UPDATE source_imports SET state='archived',receipt_id=?,asset_id=? WHERE id=?",
        (receipt.job_id, asset, import_id),
    )
    from .attachment_projection import archive_version

    archive_version(config, db, client_id, row["vault_id"], receipt.source_version_id)
    if plan["format"] == "archive" and plan["engine"] == "ordinary" and plan["action"] == "process":
        from .chat_archive import require

        require(config, db, get(config, db, client_id, import_id))
    if json.loads(row["plan_json"])["action"] == "process":
        from .automatic_jobs import enqueue

        enqueue(
            config,
            db,
            client_id,
            row["vault_id"],
            "expert" if plan["engine"] == "fake-expert" else "document",
            import_id,
        )
    return projection(db, get(config, db, client_id, import_id))


def cancel(
    config: Config, db: sqlite3.Connection, client_id: str, import_id: str
) -> dict[str, Any]:
    # Cancellation reduces authority and remains possible after a grant is revoked.
    row = db.execute(
        "SELECT * FROM source_imports WHERE id=? AND client_id=?", (import_id, client_id)
    ).fetchone()
    client = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if row is None or client is None or not client[0]:
        raise auth.Denied(404)
    if row["run_id"]:
        from .execution_ledger import transition

        transition(db, row["run_id"], "cancelled", "cancelled")
    from .expert_tasks import cancel as cancel_expert

    cancel_expert(db, client_id, import_id)
    if row["state"] != "archived":
        db.execute("UPDATE source_imports SET state='cancelled' WHERE id=?", (import_id,))
    for ordinal in range(64):
        path = contained(config.state_root, f"source-imports/{import_id}/{ordinal}.part")
        path.unlink(missing_ok=True)
    return projection(
        db, db.execute("SELECT * FROM source_imports WHERE id=?", (import_id,)).fetchone()
    )
