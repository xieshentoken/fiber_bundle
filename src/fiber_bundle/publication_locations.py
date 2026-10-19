"""Versioned physical locations; existing source ledgers retain their original semantics."""

import json
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from . import note_publications
from .config import IntegrityError

if TYPE_CHECKING:
    from .note_index import Note


def annotate(note: "Note", managed: dict[str, Any] | None) -> "Note":
    """Bind a current file only to the committed mapping and physical identity."""
    if managed is None:
        return note
    if not managed.get("file_key") or list(note.file_key or ()) != managed["file_key"]:
        raise IntegrityError("layout_rebind_required")
    article = managed["kind"] == "article"
    return replace(
        note,
        identity=managed["identity"],
        publication_id=managed["publication_id"] if article else None,
        publication_path=note.relative_path.rsplit("/", 1)[0] if article else None,
    )


def get(db: sqlite3.Connection, kind: str, identity: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM publication_locations WHERE kind=? AND publication_id=?", (kind, identity)
    ).fetchone()
    if row is None:
        return None
    plan = json.loads(row["value"])
    state = (
        "missing"
        if row["state"] == "reconcile" and plan.get("availability", {}).get("state") == "missing"
        else row["state"]
    )
    return {**dict(row), "state": state, "plan": plan}


def wire(db: sqlite3.Connection, kind: str, identity: str) -> dict[str, Any]:
    row = get(db, kind, identity)
    return (
        {k: row[k] for k in ("entry_note_path", "package_root", "revision")}
        | {
            "layout_mode": "obsidian_folders",
            "layout_version": row["plan"].get("layout_version", 1),
        }
        if row
        else {}
    )


def binding(db: sqlite3.Connection, row: sqlite3.Row) -> note_publications.Binding:
    location = get(db, "article", row["id"])
    original = note_publications.binding(row)
    if location:
        original = note_publications.Binding(
            original.publication_id,
            location["plan"]["published_hashes"][location["entry_note_path"]],
            original.marker_sha256,
        )
    return original


def note_id(kind: str, identity: str, logical: str = "note.md") -> str:
    if kind == "article":
        return "publication:" + identity
    if kind == "document":
        from .document_parts import identity as part_id

        return part_id(identity, logical)
    return "note:" + str(
        uuid5(NAMESPACE_URL, "fiber-layout:" + kind + ":" + identity + ":" + logical)
    )


def committed(db: sqlite3.Connection, row: dict[str, Any]) -> bool:
    kind = row["kind"]
    identity = row["publication_id"]
    if row["state"] != "published":
        return False
    sql = {
        "article": ("article_publications", "id"),
        "document": ("document_publications", "run_id"),
        "media": ("media_publications", "job_id"),
        "expert": ("source_expert_publications", "task_id"),
    }.get(kind)
    if sql:
        result = db.execute(f"SELECT state FROM {sql[0]} WHERE {sql[1]}=?", (identity,)).fetchone()
        return bool(result and result[0] == "published")
    # The intake publisher finalizes this row after the review sidecar commits.
    return True


def inventory(db: sqlite3.Connection, vault: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    managed = {}
    hidden = set()
    for raw in db.execute("SELECT * FROM publication_locations WHERE vault_id=?", (vault,)):
        row = dict(raw)
        plan = json.loads(row["value"])
        ready = committed(db, row)
        for logical, path in plan["mapping"].items():
            if path not in plan["outputs"] and plan.get("layout_version") != 3:
                continue
            if (
                ready
                and logical.endswith(".md")
                and logical in plan.get("body_files", list(plan["mapping"]))
                and (
                    row["package_root"]
                    or logical == "note.md"
                    or plan.get("supplement_roles", {}).get(logical) == "analysis"
                )
                and not any(p.startswith(".") for p in logical.split("/"))
            ):
                managed[path] = {
                    "kind": row["kind"],
                    "publication_id": row["publication_id"],
                    "logical": logical,
                    "identity": plan.get("identity_overrides", {}).get(
                        logical, note_id(row["kind"], row["publication_id"], logical)
                    ),
                    "package_root": row["package_root"],
                    "file_key": plan.get("file_keys", {}).get(path),
                }
            else:
                hidden.add(path)
    return managed, hidden
