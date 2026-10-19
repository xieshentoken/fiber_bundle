"""Reauthorize and compare current visible source bytes without a further search/model call."""

import sqlite3
from typing import Any

from .. import auth, note_index, note_provenance, note_search_cache, note_search_inventory
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import store
from .evidence import project


def verify(
    config: Config,
    db: sqlite3.Connection,
    cid: str,
    context: dict[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, bool]:
    row = db.execute("SELECT * FROM agent_connections WHERE client_id=?", (cid,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    current = store.context(config, db, row["id"])
    if current != context or not current["enabled"]:
        raise store.Conflict("agent_context_changed")
    if len(sources) > 10 or len(canonical(sources).encode()) > 131072:
        raise InvalidInput("agent_evidence_limit")
    scope = current["configuration"]["vault_ids"]
    inventories = {}
    for vid in scope:
        vault = auth.grant(db, cid, vid, "query")
        if note_search_cache.status(config, db, vault)["state"] == "stale":
            raise store.Conflict("agent_index_stale")
        selected = {s["note_id"] for s in sources if s.get("vault_id") == vid and s.get("note_id")}
        if any(s.get("vault_id") == vid for s in sources):
            inventories[vid] = (
                vault,
                note_search_inventory.read(config, db, vault, selected=selected),
            )
    for source in sources:
        vid = source.get("vault_id")
        if not isinstance(vid, str) or vid not in inventories:
            raise auth.Denied(404)
        vault, inventory = inventories[vid]
        from ..organization import packages
        from ..organization import store as organization_store

        module_id = source.get("expert_module_id", "")
        if source.get("expert_projection") and organization_store.get(
            config, "packages", module_id[7:]
        ):
            fresh = packages.verify_evidence(config, db, cid, source)
            envelope = {
                "scope": "vault_notes",
                "searched_vault_ids": [vid],
                "status": "evidence_found",
            }
            if project(
                {**envelope, "results": [{**source, "citation": "[1]"}]}, (vid,), 1
            ) != project({**envelope, "results": [{**fresh, "citation": "[1]"}]}, (vid,), 1):
                raise store.Conflict("agent_source_changed")
            continue
        note = next(
            (n for n in inventory.notes if n.relative_path == source.get("note_path")), None
        )
        if note is None or (source.get("note_id") and note.identity != source["note_id"]):
            raise store.Conflict("agent_source_changed")
        actual = note_index.read_note(
            inventory.root, note.relative_path, note_index.Limits().note_bytes
        )
        if actual.sha256 != source.get("sha256") or actual.file_key != note.file_key:
            raise store.Conflict("agent_source_changed")
        start, end = source.get("line"), source.get("line_end")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise InvalidInput("agent_evidence_invalid")
        snippet = "\n".join(actual.text.splitlines()[start - 1 : end])
        fresh = {
            **{k: source[k] for k in ("vault_id", "note_path", "sha256", "line", "line_end")},
            "note_id": note.identity,
            "title": actual.title,
            "snippet": snippet,
            **note_provenance.describe(config, db, cid, vault, inventory, note),
            "citation": "[1]",
        }
        from .visibility import visible

        if not visible(fresh, current["configuration"].get("quality_scope", "all")):
            raise store.Conflict("agent_source_quality_changed")
        # Compare only host-owned provenance fields, not client-supplied matching metadata.
        expected = project(
            {
                "scope": "vault_notes",
                "searched_vault_ids": [vid],
                "status": "evidence_found",
                "results": [{**source, "citation": "[1]"}],
            },
            (vid,),
            1,
        )
        checked = project(
            {
                "scope": "vault_notes",
                "searched_vault_ids": [vid],
                "status": "evidence_found",
                "results": [fresh],
            },
            (vid,),
            1,
        )
        # Older persisted answers predate the registry projection fields. They remain
        # text evidence and cannot acquire media authority merely by being reopened.
        if not any(
            key in source
            for key in ("expert_module_id", "expert_module_version", "expert_projection")
        ):
            for item in checked:
                for key in ("expert_module_id", "expert_module_version", "expert_projection"):
                    item.pop(key, None)
        if expected != checked:
            raise store.Conflict("agent_source_changed")
    return {"verified": True}
