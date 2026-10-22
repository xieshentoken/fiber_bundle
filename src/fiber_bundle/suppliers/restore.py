"""Quarantine supplier scheduling after authenticated restore; facts remain intact."""

import json

from ..config import Config
from . import store


def quarantine(config: Config) -> int:
    changed = 0
    if not (config.state_root / store.NAME).exists():
        return 0
    with store.connection(config, write=True) as db:
        assert db is not None
        profiles = db.execute("SELECT vault,value FROM profiles").fetchall()
        for vault, raw in profiles:
            value = json.loads(raw)
            if value["enabled"]:
                value["enabled"] = False
                db.execute(
                    "UPDATE profiles SET revision=revision+1,value=? WHERE vault=?",
                    (json.dumps(value), vault),
                )
                changed += 1
        for vault, kind, raw in db.execute(
            "SELECT vault,kind,value FROM records WHERE kind IN ('job','model_call')"
        ).fetchall():
            row = json.loads(raw)
            if kind == "job" and row["state"] in {"queued", "running"}:
                row.update(state="failed", error="supplier_restored_explicit_resume_required")
            elif kind == "model_call" and row["state"] == "sending":
                row["state"] = "unknown"
            else:
                continue
            store.put(db, vault, kind, row)
            changed += 1
    return changed
