"""Ownership and receipt reconciliation for resumable WeChat article capture."""

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import objects
from ..config import Config, IntegrityError, contained, make_directory
from ..inputs import canonical
from ..store import Receipt, Store


@contextmanager
def capture_lease(config: Config, identity: str) -> Iterator[bool]:
    """Cover acquisition and commit without holding the shared storage lock."""
    directory = contained(config.state_root, "execution-locks")
    make_directory(directory)
    key = objects.digest(identity.encode())
    fd = os.open(
        directory / f"channel-intake-{key}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def describe(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "sha256": objects.digest(data),
            "bytes": len(data),
            "original_name": Path(name).name,
        }
        for name, data in files.items()
    }


def stored_receipt(
    config: Config,
    db: sqlite3.Connection,
    scope: str,
    attempt: str,
    *,
    client_id: str,
    vault: str,
    source_key: str,
    batch: str,
    raw_event: bytes,
    prepared: dict[str, Any] | None,
) -> Receipt | None:
    row = db.execute(
        "SELECT * FROM receipts WHERE ingress_scope=? AND event_id=?", (scope, attempt)
    ).fetchone()
    if row is None:
        return None
    if prepared is None or row["owner_client_id"] != client_id:
        raise IntegrityError("intake capture receipt identity changed")
    if row["state"] != "stored":
        # Store.ingest_locked can finish this receipt with an identical manifest.
        return None
    data = Store(config).verify_receipt_locked(db, row["job_id"])
    assets = data["assets"]
    expected = prepared["files"]
    provenance = {
        key: prepared["metadata"][key]
        for key in (
            "original_url",
            "normalized_url",
            "redirects",
            "final_url",
            "source_sha256",
            "site",
        )
    }
    if (
        data["vault"] != vault
        or data["source_key"] != source_key
        or data["session_scope"] != "channel-intake"
        or data["session_id"] != batch
        or data["retention"] != "knowledge"
        or len(assets) != len(expected) + 3
        or assets[0]["sha256"] != objects.digest(raw_event)
        or assets[1]["original_name"] != "source.html"
        or assets[1]["sha256"] != prepared["metadata"]["source_sha256"]
        or assets[2]["original_name"] != "provenance.json"
        or assets[2]["sha256"] != objects.digest(canonical(provenance).encode())
        or sorted(
            (asset["original_name"], asset["sha256"], asset["byte_count"]) for asset in assets[3:]
        )
        != sorted(
            (asset["original_name"], asset["sha256"], asset["bytes"]) for asset in expected.values()
        )
    ):
        raise IntegrityError("intake capture receipt content changed")
    return Receipt(row["job_id"], row["state"], row["source_version_id"], row["error_code"])
