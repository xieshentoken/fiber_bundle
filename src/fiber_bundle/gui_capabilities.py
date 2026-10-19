"""GUI contracts are route/schema declarations, never evidence of runtime health."""

import sqlite3
from collections.abc import Mapping
from typing import Any


def declared(db: sqlite3.Connection, paths: Mapping[str, Any]) -> dict[str, Any]:
    gets = {path for path, methods in paths.items() if "get" in methods}
    version = db.execute("PRAGMA user_version").fetchone()[0]
    columns = {r[1] for r in db.execute("PRAGMA table_info(execution_runs)")}
    calls = {r[1] for r in db.execute("PRAGMA table_info(model_calls)")}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {
        "contract": "gui-features/v1",
        "index_summary": version >= 30 and "/admin/note-index/summary" in gets,
        "execution_attribution": version >= 30
        and "/admin/inventory/executions" in gets
        and {"binding_kind", "updated_at"} <= columns
        and {"id", "model", "price_revision", "created_at"} <= calls,
        "local_usage_breakdown": False,
        "video_processing": version >= 33
        and "video_tasks" in tables
        and "/admin/video-processing" in gets,
        "unified_workflow": version >= 31
        and {"automatic_jobs", "automatic_reviews"} <= tables
        and {
            "/admin/vault-catalog/{vault_id}/save-policy",
            "/admin/vault-catalog/{vault_id}/automatic-jobs",
            "/admin/vault-merges",
        }
        <= gets,
        "runtime_verified": False,
    }
