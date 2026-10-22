"""Barrier checks avoid large journals and retain active/unknown-state denial."""

import sqlite3

import pytest

from fiber_bundle.vault_relocation import guard
from fiber_bundle.vault_relocation.errors import RelocationError


@pytest.mark.parametrize(
    "state",
    [
        "prepared",
        "complete",
        "restored",
        "cancelled",
        "moving",
        "needs_recovery",
        "unknown-future-state",
    ],
)
def test_barrier_does_not_decode_recovery_payload(state):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE vault_operations(id TEXT,vault_id TEXT,kind TEXT,state TEXT,payload TEXT)"
    )
    payload = '{"large-recovery-payload":"' + "x" * (1024 * 1024) + '"}'
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,?,?)",
        ("op", "vault", "vault_relocation", state, payload),
    )

    def text(value):
        assert not value.startswith(b'{"large-recovery-payload"'), "recovery payload decoded"
        return value.decode()

    db.text_factory = text
    if state in guard.TERMINAL:
        assert guard.operation(db, "vault") is None
        guard.check(db, "vault")
    else:
        assert guard.operation(db, "vault")["id"] == "op"
        with pytest.raises(RelocationError, match="relocation_in_progress"):
            guard.check(db, "vault")
        with guard.executing("op"):
            guard.check(db, "vault")
        with guard.receiving("vault"):
            guard.check(db, "vault")
    assert guard.operation(db, "another-vault") is None
    db.text_factory = str
    assert db.execute("SELECT payload FROM vault_operations").fetchone()[0] == payload
    db.close()
