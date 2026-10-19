"""Owner-scoped stored session text; no raw events, binary parsing, publication or network."""

import sqlite3
from typing import Any

from . import auth, objects, vaults
from .config import Config, IntegrityError
from .inputs import text
from .search_matching import best_passage, terms
from .store import Store

MAX_RECEIPTS = 1000
MAX_BODY_BYTES = 1024 * 1024
MAX_SESSION_BYTES = 32 * 1024 * 1024


def session_search(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    session_id: str,
    query: str,
    requested: list[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Caller holds lock; session IDs are namespaced by authenticated client."""
    text(session_id, limit=400)
    patterns = terms(query)
    owner_scope = "client:" + client_id
    if requested == []:
        return []
    restriction = ""
    params: list[Any] = [client_id, owner_scope, owner_scope, session_id]
    if requested is not None:
        restriction = " AND s.vault_id IN (" + ",".join("?" for _ in requested) + ")"
        params.extend(requested)
    params.append(MAX_RECEIPTS + 1)
    rows = db.execute(
        "SELECT r.*,s.vault_id FROM receipts r "
        "JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
        "JOIN session_refs sr ON sr.version_id=v.id "
        "JOIN client_vault_grants g ON g.vault_id=s.vault_id AND g.client_id=r.owner_client_id "
        "JOIN clients c ON c.id=r.owner_client_id JOIN vaults vault ON vault.id=s.vault_id "
        "WHERE r.owner_client_id=? AND r.ingress_scope=? AND r.state='stored' "
        "AND sr.session_scope=? AND sr.session_id=? AND g.can_query=1 AND c.active=1 "
        "AND vault.status='active' AND vault.attachment_state='attached'"
        + restriction
        + " ORDER BY r.job_id LIMIT ?",
        params,
    ).fetchall()
    if len(rows) > MAX_RECEIPTS:
        raise IntegrityError("session receipt limit exceeded")
    candidates: list[tuple[int, dict[str, Any], sqlite3.Row]] = []
    total = 0
    store = Store(config)
    for row in rows:
        if requested is not None and row["vault_id"] not in requested:
            continue
        vault = auth.grant(db, client_id, row["vault_id"], "query")
        vaults.checked_vault(config, vault)
        manifest = store.verify_receipt_locked(db, row["job_id"])
        if (manifest["session_scope"], manifest["session_id"]) != (owner_scope, session_id):
            continue
        assets = db.execute(
            "SELECT a.*,va.original_name FROM version_assets va JOIN assets a ON a.id=va.asset_id "
            "WHERE va.version_id=? AND a.vault_id=? AND va.role='body' "
            "AND va.media_type IN ('text/plain','text/markdown') ORDER BY va.ordinal",
            (row["source_version_id"], row["vault_id"]),
        ).fetchall()
        for asset in assets:
            total += asset["byte_count"]
            if asset["byte_count"] > MAX_BODY_BYTES or total > MAX_SESSION_BYTES:
                raise IntegrityError("session text limit exceeded")
            content = body(config, row["vault_id"], asset)
            best = best_passage(asset["original_name"], content, patterns)
            if best is None:
                continue
            originals = []
            if vault["can_original"]:
                originals = [
                    {
                        "asset_id": asset["id"],
                        "name": asset["original_name"],
                        "url": (
                            f"/v1/vaults/{row['vault_id']}/versions/{row['source_version_id']}"
                            f"/assets/{asset['id']}"
                        ),
                    }
                ]
            item = {
                "vault_id": row["vault_id"],
                "note_path": None,
                "title": asset["original_name"],
                "sha256": asset["sha256"],
                "line": best[1],
                "snippet": best[2],
                "line_end": best[1] + len(best[2].splitlines()) - 1,
                "source_kind": "session_material",
                "source_label": "会话资料，存储完成不代表知识已核验",
                "source_url": None,
                "source_version_id": row["source_version_id"],
                "originals": originals,
                "session_id": session_id,
                "receipt_id": row["job_id"],
                "asset_id": asset["id"],
            }
            candidates.append((best[0], item, asset))
    candidates.sort(key=lambda value: (-value[0], value[1]["receipt_id"], value[1]["asset_id"]))
    results = []
    for _, item, asset in candidates[:limit]:
        auth.grant(db, client_id, item["vault_id"], "query")
        current = db.execute(
            "SELECT 1 FROM receipts r JOIN session_refs sr ON sr.version_id=r.source_version_id "
            "WHERE r.job_id=? AND r.owner_client_id=? AND r.state='stored' "
            "AND sr.session_scope=? AND sr.session_id=?",
            (item["receipt_id"], client_id, owner_scope, session_id),
        ).fetchone()
        if current is None:
            raise IntegrityError("session evidence no longer available")
        body(config, item["vault_id"], asset)
        results.append(item)
    return results


def body(config: Config, vault_id: str, asset: sqlite3.Row) -> str:
    relative = objects.object_relative(vault_id, asset["sha256"])
    if asset["relative_path"] != relative:
        raise IntegrityError("session object path mismatch")
    raw = objects.read_object(config.state_root, relative, asset["sha256"], asset["byte_count"])
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IntegrityError("session text encoding unsupported") from exc
