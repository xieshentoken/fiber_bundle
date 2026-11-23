"""Compensation reads retain owned history without loading unrelated payloads."""

import json
import sqlite3
from unittest.mock import patch

from test_jobs import ready as ready
from test_maintenance_undo import start

from fiber_bundle import database
from fiber_bundle.organization import maintenance, maintenance_undo, store


def test_payload_reads_are_scoped_bounded_and_keep_previously_owned_operations():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE vault_operations (id TEXT PRIMARY KEY, vault_id TEXT, payload TEXT)")
    relevant = [(f"operation-{i:03}", "vault", json.dumps({"n": i})) for i in range(300)]
    unrelated = "unrelated-history" * 100000
    db.executemany(
        "INSERT INTO vault_operations VALUES(?,?,?)",
        [("history", "vault", unrelated), *relevant, ("other-vault", "other", unrelated)],
    )
    expected = [row[0] for row in relevant]
    observed = []

    def row(cursor, values):
        result = sqlite3.Row(cursor, values)
        if "payload" in result.keys():
            observed.append((result["id"], len(result["payload"])))
        return result

    db.row_factory = row
    db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 130)
    result = list(
        maintenance_undo.operation_rows(
            db,
            "vault",
            {"unit_operations": ["history", expected[0]], "operations": [expected[0]]},
        )
    )
    assert [row["id"] for row in result] == expected
    assert observed == [(identity, len(payload)) for identity, _, payload in relevant]
    assert sum(length for _, length in observed) < 10000
    db.close()


def test_seal_and_cancel_keep_large_unrelated_history_without_fetching_it(config, ready):
    cid, vid, root, moc, note = ready
    historical = json.dumps({"history": "retained-evidence" * 100000})
    observed = []
    original = maintenance_undo.seal

    def seal(config, db, job):
        fetched = []
        previous = db.row_factory

        def row(cursor, values):
            result = sqlite3.Row(cursor, values)
            if "payload" in result.keys():
                fetched.append(result["id"])
            return result

        db.row_factory = row
        try:
            original(config, db, job)
        finally:
            db.row_factory = previous
        observed.append(fetched)

    before = {p: p.read_bytes() for p in (moc, note)}
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'complete',?,0,0)",
            ("retained-relocation", vid, "vault_relocation", historical),
        )
        job = start(config, db, ready)
        with patch.object(maintenance_undo, "seal", seal):
            job = maintenance.run(config, db, job)
            assert job["state"] == "running", job
            first = job["items"][0]["archive_operation"]
            job = maintenance.run(config, db, job)
            assert job["state"] == "completed", job
        assert len(observed) == 2
        assert all(
            "retained-relocation" not in fetched and first in fetched for fetched in observed
        )
        journal = store.get(config, "maintenance_undo", job["id"])
        assert first in journal["operations"]
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "cancelled", result
        assert all(path.read_bytes() == raw for path, raw in before.items())
        assert (
            db.execute(
                "SELECT payload FROM vault_operations WHERE id='retained-relocation'"
            ).fetchone()[0]
            == historical
        )
