"""Managed secrets inside authenticated backup headers, rebound only in a fresh restore."""

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import Config, IntegrityError, contained
from ..inputs import canonical
from ..query_policy_store import write_private
from ..usage_credentials import reference
from . import keychain, secrets
from .errors import ModelError


def export(config: Config) -> dict[str, str] | None:
    # Called under the complete backup's frozen storage lock; never written as plaintext.
    with sqlite3.connect(
        (config.state_root / "metadata.sqlite3").as_uri() + "?mode=ro", uri=True
    ) as db:
        if (
            not db.execute("SELECT 1 FROM sqlite_master WHERE name='model_credentials'").fetchone()
            or not db.execute("SELECT 1 FROM model_credentials").fetchone()
        ):
            return None
        _, master = secrets.master(db)
    return {"schema": "fiber-model-backup/v1", "master": base64.b64encode(master).decode()}


def validate(value: Any) -> bytes:
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "master"}
            or value["schema"] != "fiber-model-backup/v1"
        ):
            raise ValueError()
        result = base64.b64decode(value["master"], validate=True)
        if len(result) != 32:
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError):
        raise IntegrityError("restore_model_secret_invalid") from None


def rebind(config: Config, value: dict[str, str] | None) -> int:
    # Original file hashes have already passed authentication. Old task snapshots stay paused.
    with sqlite3.connect(config.state_root / "metadata.sqlite3") as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='model_credentials'").fetchone():
            return 0
        credentials = db.execute("SELECT * FROM model_credentials").fetchall()
        if not credentials:
            return 0
        if value is None:
            raise ModelError("model_backup_secret_missing")
        master = validate(value)
        root = contained(config.state_root, "model-secrets")
        staged = []
        # Verify every envelope before adding the new Keychain record or rewriting files.
        for path in root.glob("*.json"):
            data = json.loads(path.read_text())
            try:
                if (
                    data["schema"] != "fiber-model-secret/v1"
                    or path.name != f"{data['credential_id']}-{data['generation']}.json"
                ):
                    raise ValueError()
                aad = f"{data['credential_id']}:{data['generation']}".encode()
                plain = AESGCM(master).decrypt(
                    base64.b64decode(data["nonce"], validate=True),
                    base64.b64decode(data["ciphertext"], validate=True),
                    aad,
                )
                staged.append((path, data, plain, aad))
            except Exception:
                raise ModelError("model_secret_corrupt") from None
        paths = {str(p) for p, *_ in staged}
        for row in credentials:
            if str(root / Path(row["secret_file"]).name) not in paths:
                raise ModelError("model_backup_secret_missing")
        account = str(uuid4())
        keychain.put(account, master)
        for path, data, plain, aad in staged:
            import os

            nonce = os.urandom(12)
            data.update(
                account=account,
                nonce=base64.b64encode(nonce).decode(),
                ciphertext=base64.b64encode(AESGCM(master).encrypt(nonce, plain, aad)).decode(),
            )
            write_private(path, canonical(data).encode())
        db.execute(
            "UPDATE model_configuration_settings SET value=?,revision=revision+1 "
            "WHERE key='keychain'",
            (canonical(account),),
        )
        for row in credentials:
            rebound_path = str(root / Path(row["secret_file"]).name)
            db.execute(
                "UPDATE model_credentials SET secret_file=? WHERE id=?", (rebound_path, row["id"])
            )
            db.execute(
                "INSERT OR IGNORE INTO model_credential_aliases VALUES(?,?,?)",
                (rebound_path, row["id"], reference(rebound_path)["id"]),
            )
        db.execute("UPDATE model_configurations SET verification_json=NULL")
        db.execute(
            "UPDATE model_configuration_calls SET state='interrupted',"
            "error_code='model_test_restored' WHERE state IN ('queued','running')"
        )
    return len(credentials)
