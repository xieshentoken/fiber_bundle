"""Bounded compact journal queries: no recovery bodies for history or guards."""

import json
from typing import Any

from . import store

TABLE = "inbox_review_operations"
FIELDS = (
    "id",
    "created_at",
    "state",
    "action",
    "path",
    "plan_sha256",
    "restored_by",
    "restore_only",
    "error",
)


def exists(db: Any) -> bool:
    return bool(db and db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())


def history(config: Any, vault: str, client: str, path: str | None = None) -> list[Any]:
    with store.connection(config) as db:
        if not exists(db):
            return []
        assert db is not None
        columns = ",".join(f"'$.{field}'" for field in FIELDS)
        query = (
            f"SELECT json_extract(value,{columns}) FROM {TABLE} WHERE vault=? "
            "AND json_extract(value,'$.client_id')=?"
        )
        args: list[Any] = [vault, client]
        if path is not None:
            query += " AND json_extract(value,'$.path')=?"
            args.append(path)
        query += " ORDER BY json_extract(value,'$.created_at') DESC,id DESC LIMIT 501"
        return [
            {**dict(zip(FIELDS, json.loads(row[0]), strict=True)), "changes": []}
            for row in db.execute(query, args)
        ]


def pending(config: Any, vault: str, path: str, excluding: set[Any] | None = None) -> bool:
    with store.connection(config) as db:
        if not exists(db):
            return False
        assert db is not None
        return any(
            row[0] not in (excluding or set())
            for row in db.execute(
                f"SELECT id FROM {TABLE} WHERE vault=? AND json_extract(value,'$.path')=? "
                "AND json_extract(value,'$.state') IN ('applying','reconcile') LIMIT 101",
                (vault, path),
            )
        )


def quarantined(config: Any, vault: str, paths: set[str], kind: str, publication: str) -> bool:
    with store.connection(config) as db:
        if not exists(db):
            return False
        assert db is not None
        return bool(
            db.execute(
                f"SELECT 1 FROM {TABLE} WHERE vault=? "
                "AND json_extract(value,'$.action')='quarantine_empty' "
                "AND json_extract(value,'$.state') IN ('applying','reconcile','completed') "
                "AND EXISTS(SELECT 1 FROM json_each(?) WHERE value="
                f"json_extract({TABLE}.value,'$.path')) "
                f"AND EXISTS(SELECT 1 FROM json_each({TABLE}.value,'$.locations_before') old "
                "WHERE json_extract(old.value,'$.kind')=? "
                "AND json_extract(old.value,'$.publication_id')=? "
                "AND json_extract(old.value,'$.state')!='published') LIMIT 1",
                (vault, json.dumps(sorted(paths)), kind, publication),
            ).fetchone()
        )
