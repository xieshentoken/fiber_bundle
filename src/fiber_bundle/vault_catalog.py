"""Administrator-reviewed directory admission, independent of bootstrap configuration."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import auth, database, vault_scope
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical, text


def canonical_root(config: Config, value: str, scope: object) -> tuple[Path, vault_scope.Scope]:
    path = Path(value)
    if not path.is_absolute() or len(value) > 4096 or "\0" in value or ".." in path.parts:
        raise InvalidInput("vault_root_invalid")
    # Inherited denial is evaluated before probing the requested root.
    effective = vault_scope.effective(path, scope)
    from .cloud_files import require_available

    require_available(path)
    if path.resolve(strict=True) != path or not path.is_dir() or path == Path(path.anchor):
        raise InvalidInput("vault_root_invalid")
    state = config.state_root
    if path == state or path in state.parents or state in path.parents:
        raise InvalidInput("vault_root_overlaps_state")
    return path, effective


def physical_path(config: Config, row: sqlite3.Row) -> Path:
    return (
        Path(row["absolute_root"])
        if row["absolute_root"] is not None
        else config.vault_path(row["relative_path"])
    )


def checked_root(config: Config, row: sqlite3.Row) -> Path:
    try:
        path, _ = canonical_root(config, row["absolute_root"], json.loads(row["scope_json"]))
    except (TypeError, ValueError) as exc:
        raise IntegrityError("vault catalog root invalid") from exc
    info = path.stat()
    if (info.st_dev, info.st_ino) != (row["directory_dev"], row["directory_ino"]):
        raise IntegrityError("vault directory identity changed")
    return path


def overlaps(
    config: Config, db: sqlite3.Connection, path: Path, *, except_id: str | None = None
) -> None:
    for row in db.execute("SELECT * FROM vaults WHERE attachment_state='attached'"):
        if row["id"] == except_id:
            continue
        other = physical_path(config, row)
        if path == other or path in other.parents or other in path.parents:
            raise InvalidInput("vault_root_overlaps_vault")


def observe(
    config: Config,
    db: sqlite3.Connection,
    *,
    name: str,
    root: str,
    scope: object,
    read_only: bool = True,
) -> dict[str, Any]:
    text(name, limit=100)
    previous = db.execute("SELECT * FROM vaults WHERE name=?", (name,)).fetchone()
    if previous is not None and previous["attachment_state"] not in {"detached", "trashed"}:
        raise InvalidInput("vault_name_exists")
    path, effective = canonical_root(config, root, scope)
    if previous is not None:
        if path != physical_path(config, previous):
            raise InvalidInput("vault_reattach_path_changed")
        info = path.stat()
        if previous["directory_ino"] is not None and (info.st_dev, info.st_ino) != (
            previous["directory_dev"],
            previous["directory_ino"],
        ):
            raise InvalidInput("vault_reattach_identity_changed")
    overlaps(config, db, path, except_id=previous["id"] if previous else None)
    from .vault_files import inventory

    observed = inventory(path, effective)
    info = path.stat()
    configured_scope = {
        "include": list(effective.include),
        "exclude": list(vault_scope.parse(scope).exclude),
    }
    value: dict[str, Any] = {
        "name": name,
        "existing_id": previous["id"] if previous else None,
        "root": str(path),
        "scope": configured_scope,
        "effective_scope": effective.wire(),
        "read_only": read_only,
        "directory_dev": info.st_dev,
        "directory_ino": info.st_ino,
        "inventory": observed,
    }
    return value


def persist_preview(
    config: Config, db: sqlite3.Connection, value: dict[str, Any]
) -> dict[str, Any]:
    path, scope = canonical_root(config, value["root"], value["scope"])
    info = path.stat()
    if (info.st_dev, info.st_ino) != (
        value["directory_dev"],
        value["directory_ino"],
    ) or scope.wire() != value["effective_scope"]:
        raise InvalidInput("vault_preview_changed")
    previous = db.execute("SELECT * FROM vaults WHERE name=?", (value["name"],)).fetchone()
    if previous is not None and (
        previous["id"] != value["existing_id"]
        or previous["attachment_state"] not in {"detached", "trashed"}
    ):
        raise InvalidInput("vault_name_exists")
    overlaps(config, db, path, except_id=value["existing_id"])
    observed = value["inventory"]
    # Wire scopes separate configured prefixes from inherited policy evidence.
    identity = str(uuid4())
    db.execute("DELETE FROM vault_admissions WHERE expires_at<?", (time.time(),))
    db.execute(
        "INSERT INTO vault_admissions(id,payload,expires_at) VALUES(?,?,?)",
        (identity, canonical(value), time.time() + 600),
    )
    return {
        "preview_id": identity,
        **{k: v for k, v in value.items() if k != "inventory"},
        **{k: v for k, v in observed.items() if k != "files"},
        "expires_in": 600,
        "model_calls": 0,
    }


def preview(config: Config, db: sqlite3.Connection, **kwargs: Any) -> dict[str, Any]:
    return persist_preview(config, db, observe(config, db, **kwargs))


def validate_admission(config: Config, db: sqlite3.Connection, preview_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_admissions WHERE id=? AND consumed=0 AND expires_at>?",
        (preview_id, time.time()),
    ).fetchone()
    if row is None:
        raise InvalidInput("vault_preview_expired")
    value: dict[str, Any] = json.loads(row["payload"])
    path, scope = canonical_root(config, value["root"], value["scope"])
    from .vault_files import inventory

    if inventory(path, scope) != value["inventory"]:
        raise InvalidInput("vault_preview_changed")
    return value


def attach(
    config: Config,
    db: sqlite3.Connection,
    preview_id: str,
    *,
    validated: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_admissions WHERE id=? AND consumed=0 AND expires_at>?",
        (preview_id, time.time()),
    ).fetchone()
    if row is None:
        raise InvalidInput("vault_preview_expired")
    value: dict[str, Any] = json.loads(row["payload"])
    path, scope = canonical_root(config, value["root"], value["scope"])
    info = path.stat()
    if (info.st_dev, info.st_ino) != (
        value["directory_dev"],
        value["directory_ino"],
    ) or scope.wire() != value["effective_scope"]:
        raise InvalidInput("vault_preview_changed")
    if validated is None:
        validate_admission(config, db, preview_id)
    elif validated != value:
        raise InvalidInput("vault_preview_changed")
    existing = value.get("existing_id")
    overlaps(config, db, path, except_id=existing)
    identity = existing or str(uuid4())
    with database.transaction(db):
        previous = db.execute("SELECT * FROM vaults WHERE name=?", (value["name"],)).fetchone()
        if existing:
            if (
                previous is None
                or previous["id"] != existing
                or previous["attachment_state"] not in {"detached", "trashed"}
            ):
                raise InvalidInput("vault_preview_changed")
            db.execute(
                "UPDATE vaults SET attachment_state='attached',read_only=?,scope_json=?,"
                "directory_dev=?,directory_ino=? WHERE id=?",
                (
                    int(value["read_only"]),
                    canonical(value["scope"]),
                    info.st_dev,
                    info.st_ino,
                    existing,
                ),
            )
        else:
            if previous is not None:
                raise InvalidInput("vault_name_exists")
            db.execute(
                "INSERT INTO vaults(id,name,relative_path,absolute_root,root_kind,"
                "read_only,scope_json,directory_dev,directory_ino) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    value["name"],
                    "vaults/" + identity,
                    str(path),
                    "external",
                    int(value["read_only"]),
                    canonical(value["scope"]),
                    info.st_dev,
                    info.st_ino,
                ),
            )
        db.execute("UPDATE vault_admissions SET consumed=1 WHERE id=?", (preview_id,))
    return {
        "vault_id": identity,
        "state": "attached",
        "index_state": "not_started",
        "model_calls": 0,
    }


def unlink(
    config: Config, db: sqlite3.Connection, vault_id: str, *, keep_operation: str | None = None
) -> dict[str, Any]:
    from .vault_merge_guard import check

    check(db, vault_id)
    row = db.execute("SELECT * FROM vaults WHERE id=?", (vault_id,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    if row["attachment_state"] == "detached":
        return {"vault_id": vault_id, "state": "detached", "files_modified": False}
    if row["attachment_state"] != "attached":
        raise InvalidInput("vault_operation_requires_reconciliation")
    from . import query_jobs
    from .channels.store import invalidate_vault

    invalidate_vault(config, vault_id)

    # Stop owned model admissions before withdrawing their scope; reserved costs stay uncertain.
    for job in db.execute(
        "SELECT q.* FROM query_jobs q JOIN clients c ON c.id=q.client_id "
        "LEFT JOIN local_query_tasks t ON t.id=q.task_id "
        "LEFT JOIN execution_runs r ON r.id=q.task_id "
        "WHERE c.active=1 AND q.state IN ('reserved','running') "
        "AND (r.vault_id=? OR t.vault_id=? OR EXISTS "
        "(SELECT 1 FROM json_each(t.roots_json) WHERE json_extract(value,'$.vault_id')=?))",
        (vault_id, vault_id, vault_id),
    ).fetchall():
        query_jobs.cancel(config, db, job["client_id"], job["id"])
    from .config import contained
    from .knowledge_graph import store

    if contained(config.state_root, "knowledge-graph.sqlite3").exists():
        with store.connection(config, write=True) as graph:
            assert graph is not None
            with database.transaction(graph):
                graph.execute(
                    "UPDATE jobs SET state='cancelled',finished_at=? "
                    "WHERE vault_id=? AND state IN ('queued','running')",
                    (time.time(), vault_id),
                )
                graph.execute("UPDATE generations SET state='stale' WHERE vault_id=?", (vault_id,))
    with database.transaction(db):
        db.execute(
            "UPDATE vaults SET attachment_state='detached',default_query=0 WHERE id=?", (vault_id,)
        )
        db.execute(
            "UPDATE vault_operations SET state='cancelled',updated_at=? "
            "WHERE vault_id=? AND state IN ('prepared','launch_requested','running') "
            "AND (? IS NULL OR id!=?)",
            (time.time(), vault_id, keep_operation, keep_operation),
        )
        # Existing SQL permission triggers advance graph epochs and invalidate work.
        db.execute(
            "UPDATE client_vault_grants SET can_query=0,can_original=0,can_add=0 WHERE vault_id=?",
            (vault_id,),
        )
        identity = str(uuid4())
        now = time.time()
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,?,?,?,?)",
            (
                identity,
                vault_id,
                "unlink",
                "complete",
                canonical({"files_modified": False}),
                now,
                now,
            ),
        )
    return {"vault_id": vault_id, "state": "detached", "files_modified": False}
