"""Authenticated instance secrets; immutable generation files and Keychain master key."""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from ..config import Config, contained, make_directory
from ..inputs import canonical
from ..query_policy_store import write_private
from ..query_routing_files import read as bounded_read
from . import keychain
from .errors import ModelError


def master(db: sqlite3.Connection, *, create: bool = False) -> tuple[str, bytes]:
    row = db.execute(
        "SELECT value FROM model_configuration_settings WHERE key='keychain'"
    ).fetchone()
    if row:
        account = json.loads(row[0])
        return account, keychain.get(account)
    if not create:
        raise ModelError("model_keychain_unavailable")
    account, key = str(uuid4()), os.urandom(32)
    keychain.put(account, key)
    db.execute(
        "INSERT INTO model_configuration_settings VALUES('keychain',1,?)", (canonical(account),)
    )
    return account, key


def fingerprint(key: bytes, connection: dict[str, str], secret: str) -> str:
    return hmac.new(
        key, canonical({**connection, "key": secret}).encode(), hashlib.sha256
    ).hexdigest()


def seal(
    config: Config, identity: str, generation: int, account: str, key: bytes, value: str
) -> Path:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    root = contained(config.state_root, "model-secrets")
    make_directory(root)
    nonce = os.urandom(12)
    aad = f"{identity}:{generation}".encode()
    envelope = {
        "schema": "fiber-model-secret/v1",
        "account": account,
        "credential_id": identity,
        "generation": generation,
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(AESGCM(key).encrypt(nonce, value.encode(), aad)).decode(),
    }
    path = contained(root, f"{identity}-{generation}.json")
    if path.exists():
        raise ModelError("model_revision_conflict")
    write_private(path, canonical(envelope).encode())
    return path


def load(path: Path, *, check_generation: bool = True) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        data = json.loads(bounded_read(path, 16384, private=True))
        if data.get("schema") != "fiber-model-secret/v1" or path.parent.name != "model-secrets":
            raise ModelError("model_key_format")
        if check_generation:
            with sqlite3.connect(
                (path.parent.parent / "metadata.sqlite3").as_uri() + "?mode=ro", uri=True
            ) as db:
                row = db.execute(
                    "SELECT generation,active,secret_file FROM model_credentials WHERE id=?",
                    (data["credential_id"],),
                ).fetchone()
            if not row or row != (data["generation"], 1, str(path)):
                raise ModelError("model_credential_revoked")
        key = keychain.get(data["account"])
        aad = f"{data['credential_id']}:{data['generation']}".encode()
        return (
            AESGCM(key)
            .decrypt(base64.b64decode(data["nonce"]), base64.b64decode(data["ciphertext"]), aad)
            .decode()
        )
    except ModelError:
        raise
    except (InvalidTag, ValueError, KeyError, UnicodeError):
        raise ModelError("model_secret_corrupt") from None
    except Exception:
        raise ModelError("model_keychain_unavailable") from None
