"""Explicit partial write scope: omit frozen ordinary sources, never infer their contents."""

import sqlite3
from dataclasses import replace
from typing import Any

from . import note_changes, note_identity_store, note_inventory, vaults
from .config import Config, IntegrityError


def validate(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 100:
        raise IntegrityError("partial_note_scope_invalid")
    for path, reason in value.items():
        if not isinstance(path, str) or not isinstance(reason, str) or len(reason) > 180:
            raise IntegrityError("partial_note_scope_invalid")
        note_changes.validate([{"identity": "path:" + path, "note_path": path, "sha256": "0" * 64}])
        if not path.lower().endswith(".md"):
            raise IntegrityError("partial_note_scope_invalid")
    return value


def read(
    config: Config,
    db: sqlite3.Connection,
    vault: sqlite3.Row,
    omissions: dict[str, str] | None = None,
    **kwargs: Any,
) -> note_inventory.Inventory:
    if not omissions:
        return note_inventory.read(config, db, vault, **kwargs)
    errors = dict(validate(omissions))
    for path in errors:
        vaults.require_scope(config, vault, path)
    inventory = note_inventory.read(config, db, vault, note_errors=errors, **kwargs)
    # A newly failing source is outside the confirmed scope, including a MOC.
    if errors != omissions:
        raise IntegrityError("partial_note_scope_changed")
    members = {
        r.identity: r.relative_path
        for r in note_identity_store.records(config, vault["id"])
        if r.relative_path in omissions
    }
    return replace(inventory, coverage={"omissions": omissions, "member_paths": members})


def for_job(job: Any) -> dict[str, str] | None:
    if job["fixed"].get("unavailable_action") not in {"moc_only", "defer_links"}:
        return None
    coverage = job["fixed"].get("coverage", {})
    if coverage.get("unavailable_count", 0) > len(coverage.get("unavailable", [])):
        raise IntegrityError("partial_note_scope_truncated")
    frozen = {i["path"]: i["reason"] for i in coverage.get("unavailable", [])}
    dynamic = validate(job.get("runtime_omissions", {}))
    ordinary = {i["path"] for i in job["items"] if i["kind"] == "note"}
    if not set(dynamic) <= ordinary:
        raise IntegrityError("partial_note_scope_changed")
    return validate({**frozen, **dynamic}) or None
