"""Shared read-only blockers for publication, inventory and index consumers."""

import sqlite3
from typing import Any

from .config import Config, IntegrityError, StorageError

KINDS = (
    "note_placement",
    "layout_migration",
    "publication_rebind",
    "maintenance_link_repair",
    "maintenance_archive",
)


def pending(db: sqlite3.Connection, vault: str) -> list[dict[str, str]]:
    return [
        dict(id=row[0], kind=row[1], state=row[2])
        for row in db.execute(
            "SELECT id,kind,state FROM vault_operations WHERE vault_id=? AND kind IN ("
            + ",".join("?" for _ in KINDS)
            + ") AND state IN ('applying','reconcile') ORDER BY created_at,id",
            (vault, *KINDS),
        )
    ]


class Required(IntegrityError):
    def __init__(
        self, blockers: list[dict[str, str]], dependency: dict[str, Any] | None = None
    ) -> None:
        super().__init__("placement_reconciliation_required")
        self.blockers = blockers
        self.dependency = dependency or {
            "reason": "placement_reconciliation_required",
            "operations": [{**row, "job_ids": []} for row in blockers],
            "recovery": "review_original_operation",
        }


def describe(config: Config, db: sqlite3.Connection, vault: str) -> dict[str, Any] | None:
    blockers = pending(db, vault)
    if not blockers:
        return None
    from .organization import store

    owner_lookup_failed = False
    try:
        owners = store.archive_owners(config, vault, [row["id"] for row in blockers])
    except (OSError, ValueError, TypeError, sqlite3.Error, StorageError):
        owners = {}
        owner_lookup_failed = True
    owned_archive = any(
        row["kind"] in {"note_placement", "maintenance_archive"} and owners.get(row["id"])
        for row in blockers
    )
    return {
        "reason": "placement_reconciliation_required",
        "operations": [
            {**row, "job_ids": sorted(set(owners.get(row["id"], [])))} for row in blockers
        ],
        "recovery": "verify_committed_archives" if owned_archive else "review_original_operation",
        **({"owner_lookup_failed": True} if owner_lookup_failed else {}),
    }


def check(config: Config, db: sqlite3.Connection, vault: str) -> None:
    dependency = describe(config, db, vault)
    if dependency:
        raise Required(pending(db, vault), dependency)


def response(error: Exception) -> tuple[int, dict[str, Any]] | None:
    if isinstance(error, Required):
        return 409, {"error": str(error), "dependency": error.dependency}
    return None
