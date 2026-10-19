"""Exact journal-to-source reuse and atomic cancellation of unstarted ordinary requests."""

import json
import sqlite3
from typing import Any

from . import source_imports
from .config import Config, InvalidInput
from .objects import digest


def receipt(
    db: sqlite3.Connection,
    journal: sqlite3.Connection,
    job: str,
    client: str,
    vault: str,
    name: str,
    raw: bytes,
) -> tuple[str, str] | None:
    if not journal.execute("SELECT 1 FROM sqlite_master WHERE name='wx_store_handoffs'").fetchone():
        return None
    found = set()
    for handoff in journal.execute("SELECT * FROM wx_store_handoffs WHERE state='stored'"):
        plan = json.loads(handoff["plan_json"])
        if not any(s.get("journal_job_id") == job for s in plan["sources"]):
            continue
        for saved in json.loads(handoff["results_json"]):
            for item in db.execute(
                "SELECT r.job_id,va.asset_id FROM receipts r JOIN source_versions v "
                "ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
                "JOIN version_assets va ON va.version_id=v.id JOIN assets a ON a.id=va.asset_id "
                "WHERE r.job_id=? AND r.owner_client_id=? AND s.vault_id=? AND r.state='stored' "
                "AND va.original_name=? AND a.sha256=? AND a.byte_count=?",
                (saved["job_id"], client, vault, name, digest(raw), len(raw)),
            ):
                found.add(tuple(item))
    if len(found) > 1:
        raise InvalidInput("expert_feishu_source_mapping_ambiguous")
    return next(iter(found)) if found else None


def stop_unstarted(
    config: Config, db: sqlite3.Connection, client: str, receipt_id: str, asset: str
) -> list[dict[str, Any]]:
    results = []
    for row in db.execute(
        "SELECT * FROM source_imports WHERE client_id=? AND receipt_id=? AND asset_id=?",
        (client, receipt_id, asset),
    ).fetchall():
        if json.loads(row["plan_json"])["engine"] != "ordinary":
            continue
        run = db.execute("SELECT state FROM execution_runs WHERE id=?", (row["run_id"],)).fetchone()
        launch = db.execute(
            "SELECT launch_state FROM source_task_bindings WHERE run_id=?", (row["run_id"],)
        ).fetchone()
        unstarted = run is None or (run[0] == "queued" and launch and launch[0] == "prepared")
        if unstarted and row["state"] != "cancelled":
            source_imports.cancel(config, db, client, row["id"])
            db.execute("UPDATE source_imports SET state='cancelled' WHERE id=?", (row["id"],))
            db.execute(
                "UPDATE automatic_jobs SET state='cancelled',reason=? WHERE source_id=? "
                "AND kind='document' AND state!='complete'",
                ("explicit_expert_request_replaced_unstarted_intent", row["id"]),
            )
        results.append(
            {
                "import_id": row["id"],
                "run_id": row["run_id"],
                "scope": "unstarted_cancelled" if unstarted else "existing_retained_extra_expert",
            }
        )
    return results
