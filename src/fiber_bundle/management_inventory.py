"""Bounded administrative projections; original bytes keep existing client scope checks."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import operations
from .config import Config
from .database import SCHEMA_VERSION


def receipts(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT job_id,state,source_version_id,error_code,owner_client_id FROM receipts "
            "ORDER BY rowid DESC LIMIT 100"
        )
    ]


def executions(db: sqlite3.Connection) -> dict[str, Any]:
    runs = [
        dict(row)
        for row in db.execute(
            "SELECT id,client_id,state,attempts,error_code,created_at,"
            "task_limit_nusd,config_digest,binding_kind,updated_at "
            "FROM execution_runs ORDER BY created_at DESC,id LIMIT 101"
        )
    ]
    has_more = len(runs) > 100
    runs = runs[:100]
    for row in runs:
        row["calls"] = [
            dict(call)
            for call in db.execute(
                "SELECT "
                "id,model,price_revision,created_at,state,reserved_nusd,charged_nusd,"
                "billing_day,currency FROM model_calls "
                "WHERE run_id=? ORDER BY rowid LIMIT 101",
                (row["id"],),
            )
        ]
        row["calls_has_more"] = len(row["calls"]) > 100
        row["calls"] = row["calls"][:100]
    totals = [
        dict(r)
        for r in db.execute(
            "SELECT billing_day,currency,count(*) AS calls,sum(CASE WHEN state='settled' THEN "
            "charged_nusd ELSE 0 END) AS settled_nusd,"
            "sum(CASE WHEN state!='settled' THEN reserved_nusd ELSE 0 END) AS "
            "reserved_or_unknown_nusd "
            "FROM model_calls GROUP BY billing_day,currency ORDER BY billing_day DESC LIMIT 100"
        )
    ]
    return {"runs": runs, "daily_costs": totals, "limit": 100, "has_more": has_more}


def versions(
    config: Config, db: sqlite3.Connection, client: str, vault: str, offset: int
) -> list[dict[str, Any]]:
    return operations.versions(config, db, client, vault, offset, 20)


def components() -> dict[str, Any]:
    root = Path(__file__).parent
    result = []
    for name in (
        "article-source.json",
        "bili-note-source.json",
        "fake-expert-source.json",
        "asr-runtime.json",
        "asr-model-lock.json",
    ):
        path = root / "vendor" / name
        value = json.loads(path.read_bytes())
        result.append(
            {
                "name": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "revision": value.get(
                    "revision",
                    value.get("version", value.get("upstream_sha256", value.get("source_sha256"))),
                ),
            }
        )
    from .build_identity import inspect as identity

    return {
        "package": identity().get("version", "development"),
        "candidate_schema": SCHEMA_VERSION,
        "components": result,
        "update_automatic": False,
        "running_configuration": "immutable",
        "qualification": "candidate",
    }


def check_components() -> dict[str, Any]:
    """Explicit local version check. No upstream lookup, installation or running-task mutation."""
    root = Path(__file__).parent / "vendor"
    checks = []
    for manifest, implementation in (
        ("article-source.json", "article_parser.py"),
        ("article-source.json", "wechat_image_message.py"),
        ("fake-expert-source.json", "fake_expert_pdf.py"),
    ):
        value = json.loads((root / manifest).read_bytes())
        record = value.get("extensions", {}).get(implementation, value)
        expected = record["adapted_sha256"]
        actual = hashlib.sha256((root / implementation).read_bytes()).hexdigest()
        checks.append(
            {"name": implementation, "state": "matched" if expected == actual else "changed"}
        )
    runtime = json.loads((root / "asr-runtime.json").read_bytes())
    actual = hashlib.sha256((root / "asr-model-lock.json").read_bytes()).hexdigest()
    checks.append(
        {
            "name": "asr-model-lock.json",
            "state": "matched" if runtime["model_lock_sha256"] == actual else "changed",
        }
    )
    return {
        "state": "matched" if all(c["state"] == "matched" for c in checks) else "changed",
        "checks": checks,
        "upstream": "not_checked",
        "asr_weights": "not_checked",
        "installation": "not_requested",
        "running_tasks_changed": False,
    }
