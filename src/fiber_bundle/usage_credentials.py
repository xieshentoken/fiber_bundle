"""Stable credential-configuration references. Never open or fingerprint secret contents."""

import hashlib
import sqlite3
from typing import Any


def reference(secret_file: str, db: sqlite3.Connection | None = None) -> dict[str, str]:
    identity = "key_" + hashlib.sha256(("opencodego\0" + secret_file).encode()).hexdigest()
    label = "OpenCodeGo · Key " + identity[-8:]
    provider = "opencodego"
    if db is not None:
        import json

        managed = db.execute(
            "SELECT settings_json FROM model_credentials WHERE secret_file=?", (secret_file,)
        ).fetchone()
        if managed:
            provider = json.loads(managed[0])["provider"]
        from . import model_budgets

        mapped = model_budgets.key(db, secret_file)
        if mapped is not None:
            identity = mapped
            saved = model_budgets.get(db, identity)
            assert saved is not None
            label = saved["settings"]["label"]
    return {"id": identity, "label": label, "provider": provider}


def record(db: sqlite3.Connection, call_id: str, secret_file: str) -> None:
    """Caller includes this snapshot in the same transaction as the call reservation."""
    key = reference(secret_file, db)
    db.execute(
        "INSERT INTO model_call_credentials VALUES(?,?,?,?)",
        (call_id, key["id"], key["label"], key["provider"]),
    )


def from_model(model: dict[str, Any], db: sqlite3.Connection | None = None) -> dict[str, str]:
    key = reference(model["secret_file"], db)
    return {"credential_id": key["id"], "credential_label": key["label"]}
