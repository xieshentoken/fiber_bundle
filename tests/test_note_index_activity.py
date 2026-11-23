"""Global index progress is local, read-only display evidence, including under load."""

import json
import threading
import time
from unittest.mock import patch

import pytest

from fiber_bundle import database, note_index_activity, note_search_jobs, vault_scope
from fiber_bundle.vault_sync import store as sync

NOW = 1000.0


@pytest.fixture
def tracked(config, store):
    with database.connect(config.state_root) as db:
        rows = db.execute("SELECT id,name FROM vaults ORDER BY name").fetchall()
    ids = [row["id"] for row in rows]
    for identity in ids:
        # Registration here seeds only a synthetic local ledger, never Vault contents.
        sync.register(config, identity, "/synthetic-unused-vault", vault_scope.Scope(), "bound")
        with sync.connection(config, write=True) as side:
            side.execute("DELETE FROM pending WHERE vault=?", (identity,))
            side.execute(
                "UPDATE libraries SET baseline=1,generation=5,indexed=5,heartbeat=?,"
                "checked_at=?,state='ready' WHERE id=?",
                (NOW - 1, NOW - 2, identity),
            )
        with patch("fiber_bundle.note_search_jobs.time.time", return_value=NOW - 2):
            note_search_jobs.record(config, identity, "ready", reason="completed")
    return ids


def snapshot(config):
    with database.connect(config.state_root, readonly=True) as db:
        with patch("fiber_bundle.note_index_activity.time.time", return_value=NOW):
            return note_index_activity.snapshot(config, db)


def modify(config, identity, sql, parameters=()):
    with sync.connection(config, write=True) as side:
        side.execute(sql, (*parameters, identity))


def record(config, identity, state, at=NOW - 1):
    with patch("fiber_bundle.note_search_jobs.time.time", return_value=at):
        note_search_jobs.record(config, identity, state, reason="source_changed")


def test_current_activity_preserves_every_attached_active_vault_and_has_no_paths(config, tracked):
    value = snapshot(config)
    assert value["schema"] == "note-index-activity/v1"
    assert value["observed_at"] == NOW
    assert len(value["vaults"]) == len(tracked)
    for item in value["vaults"]:
        assert set(item) == {
            "vault_id",
            "name",
            "state",
            "pending_paths",
            "needs_attention",
            "updated_at",
        }
        assert item["state"] == "ready"
        assert item["pending_paths"] == item["needs_attention"] == 0
    assert "/synthetic" not in str(value)


def test_explicit_package_stage_is_visible_before_index_publication(config, tracked):
    from fiber_bundle.vault_sync import explicit_refresh

    with patch.object(
        explicit_refresh, "activity", return_value={"stage": "packages", "state": "queued"}
    ):
        value = snapshot(config)
    assert all(row["state"] == "queued" and row["phase"] == "packages" for row in value["vaults"])


@pytest.mark.parametrize("attachment", ["detached", "trashed", "trash_pending"])
def test_detached_or_trashed_old_active_row_is_not_a_global_ghost(config, tracked, attachment):
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vaults SET attachment_state=? WHERE id=?", (attachment, tracked[0]))
    assert [v["vault_id"] for v in snapshot(config)["vaults"]] == tracked[1:]


def test_state_transitions_do_not_let_historical_failures_hide_current_work(config, tracked):
    identity = tracked[0]
    record(config, identity, "failed", NOW - 100)
    modify(config, identity, "UPDATE libraries SET generation=6,state='pending' WHERE id=?")
    with sync.connection(config, write=True) as side:
        side.execute(
            "INSERT INTO pending VALUES(?,?,?,?,?,?)", (identity, "secret.png", 0, 6, NOW, 0)
        )
    assert snapshot(config)["vaults"][0]["state"] == "observing"
    modify(config, identity, "DELETE FROM pending WHERE vault=?")
    modify(config, identity, "UPDATE libraries SET state='index_pending' WHERE id=?")
    assert snapshot(config)["vaults"][0]["state"] == "queued"
    modify(config, identity, "UPDATE libraries SET error='vault_sync_index_capacity' WHERE id=?")
    record(config, identity, "building")
    assert snapshot(config)["vaults"][0]["state"] == "building"
    record(config, identity, "building", NOW - note_index_activity.BUILD_AGE - 1)
    assert snapshot(config)["vaults"][0]["state"] == "unverified"
    record(config, identity, "waiting")
    assert snapshot(config)["vaults"][0]["state"] == "failed"
    modify(config, identity, "UPDATE libraries SET error='vault_sync_cloud_pending' WHERE id=?")
    assert snapshot(config)["vaults"][0]["state"] == "waiting"
    modify(
        config,
        identity,
        "UPDATE libraries SET error=NULL,indexed=generation,state='attention' WHERE id=?",
    )
    with sync.connection(config, write=True) as side:
        side.execute(
            "INSERT INTO issues VALUES(?,?,?,?,?,?)", (identity, "private.md", "review", 6, NOW, 1)
        )
    record(config, identity, "partial")
    assert snapshot(config)["vaults"][0]["state"] == "needs_review"
    modify(config, identity, "DELETE FROM issues WHERE vault=?")
    modify(config, identity, "UPDATE libraries SET state='ready' WHERE id=?")
    record(config, identity, "ready")
    assert snapshot(config)["vaults"][0]["state"] == "ready"


def test_cooldown_and_pending_retry_are_waiting_not_ready(config, tracked):
    identity = tracked[0]
    modify(
        config, identity, "UPDATE libraries SET generation=6,index_after=? WHERE id=?", (NOW + 60,)
    )
    assert snapshot(config)["vaults"][0]["state"] == "waiting"
    modify(config, identity, "UPDATE libraries SET indexed=generation,index_after=0 WHERE id=?")
    with sync.connection(config, write=True) as side:
        side.execute(
            "INSERT INTO pending VALUES(?,?,?,?,?,?)", (identity, "private.md", 0, 6, NOW + 60, 1)
        )
    item = snapshot(config)["vaults"][0]
    assert item["state"] == "waiting" and item["pending_paths"] == 1


def test_absent_or_stale_observer_and_damaged_job_are_not_ready(config, tracked):
    identity = tracked[0]
    modify(config, identity, "UPDATE libraries SET heartbeat=? WHERE id=?", (NOW - 45,))
    assert snapshot(config)["vaults"][0]["state"] == "unverified"
    modify(config, identity, "UPDATE libraries SET heartbeat=? WHERE id=?", (NOW - 1,))
    path = config.state_root / "note-search" / (identity + ".job.json")
    path.write_bytes(b'{"bad":"/private/path must not leak"}')
    rows = snapshot(config)["vaults"]
    assert rows[0]["state"] == "unverified"
    assert rows[1]["state"] == "ready"
    assert "/private/path" not in str(rows)
    path.unlink()
    assert snapshot(config)["vaults"][0]["state"] == "unverified"
    modify(config, identity, "DELETE FROM libraries WHERE id=?")
    assert snapshot(config)["vaults"][0]["pending_paths"] is None


def test_no_side_ledger_is_an_explicit_unknown_without_creating_it(config, store):
    path = config.state_root / sync.NAME
    assert not path.exists()
    value = snapshot(config)
    assert value["vaults"] and all(v["state"] == "unverified" for v in value["vaults"])
    assert not path.exists()


def test_unsupported_or_invalid_side_ledger_never_returns_ready(config, tracked):
    modify(config, tracked[0], "UPDATE libraries SET indexed=generation+1 WHERE id=?")
    rows = snapshot(config)["vaults"]
    assert rows[0]["state"] == "unverified" and rows[0]["updated_at"] is None
    assert rows[1]["state"] == "ready"
    with sync.connection(config, write=True) as side:
        side.execute("PRAGMA user_version=999")
    assert all(v["state"] == "unverified" for v in snapshot(config)["vaults"])


@pytest.mark.parametrize(
    "field,value", [("checked_at", float("inf")), ("heartbeat", -1), ("generation", -2)]
)
def test_malformed_numeric_ledger_row_is_isolated_to_safe_unknown(config, tracked, field, value):
    modify(config, tracked[0], f"UPDATE libraries SET {field}=? WHERE id=?", (value,))
    result = snapshot(config)
    rows = result["vaults"]
    assert rows[0]["state"] == "unverified"
    assert rows[0]["pending_paths"] is None
    assert rows[0]["needs_attention"] is None
    assert rows[0]["updated_at"] is None
    assert rows[1]["state"] == "ready"
    json.dumps(result, allow_nan=False)


def test_activity_http_is_read_only_and_never_opens_vault_or_search_index(config, http, tracked):
    paths = [
        config.state_root / sync.NAME,
        *sorted((config.state_root / "note-search").glob("*.job.json")),
    ]
    before = {p: p.read_bytes() for p in paths}
    with (
        patch("fiber_bundle.note_index_activity.time.time", return_value=NOW),
        patch("fiber_bundle.vaults.checked_vault", side_effect=AssertionError("Vault read")),
        patch(
            "fiber_bundle.note_search_cache.connection", side_effect=AssertionError("index read")
        ),
        patch(
            "fiber_bundle.note_search_jobs.changed", side_effect=AssertionError("scheduled update")
        ),
    ):
        for _ in range(3):
            result = http.get("/admin/note-index/activity")
            assert result.status_code == 200, result.text
            assert result.headers["cache-control"] == "no-store"
            assert len(result.json()["vaults"]) == 2
    assert {p: p.read_bytes() for p in paths} == before


def test_activity_http_does_not_wait_for_the_long_filesystem_writer(config, http, tracked):
    entered, release = threading.Event(), threading.Event()

    def hold_writer():
        with database.locked(config):
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=hold_writer)
    thread.start()
    assert entered.wait(2)
    try:
        started = time.monotonic()
        result = http.get("/admin/note-index/activity")
        assert result.status_code == 200, result.text
        assert time.monotonic() - started < 2
    finally:
        release.set()
        thread.join(2)


def test_activity_http_requires_admin_session_and_same_origin(http):
    assert (
        http.get(
            "/admin/note-index/activity", headers={"Origin": "https://other.invalid"}
        ).status_code
        == 403
    )
    http.cookies.clear()
    assert http.get("/admin/note-index/activity").status_code == 401


def test_metadata_writer_returns_bounded_busy_without_leaking_or_mutating(config, http, tracked):
    entered, release = threading.Event(), threading.Event()

    def hold_metadata():
        with database.connect(config.state_root) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            entered.set()
            assert release.wait(5)
            db.rollback()

    thread = threading.Thread(target=hold_metadata)
    thread.start()
    assert entered.wait(2)
    try:
        started = time.monotonic()
        result = http.get("/admin/note-index/activity")
        assert result.status_code == 503
        assert result.json() == {"error": "storage_busy"}
        assert time.monotonic() - started < 2
    finally:
        release.set()
        thread.join(2)
