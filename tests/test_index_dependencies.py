"""Dependencies wait for their owning operation; ordinary IO retries stay independent."""

import json
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_note_changes import scope as scope
from test_vault_sync_reconcile import observe

from fiber_bundle import database, note_index_activity, note_search_jobs, operation_dependencies
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_sync import index, maintenance, presentation, store


def block(config, vault, kind="note_placement"):
    identity = str(uuid4())
    with database.connect(config.state_root) as db:
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'reconcile',?,1,1)",
            (identity, vault, kind, json.dumps({"metadata_committed": True})),
        )
    return identity


def dirty(config, scope):
    client, vid, root = scope
    observe(config, (client, vid, root))
    with store.connection(config, write=True) as side:
        side.execute(
            "UPDATE libraries SET state='index_pending',index_after=?,index_attempts=4,"
            "heartbeat=? WHERE id=?",
            (time.time() + 1800, time.time(), vid),
        )


def test_dependency_wait_preserves_attempts_and_wakes_after_all_blockers(config, scope):
    client, vid, _ = scope
    cid = client["id"]
    dirty(config, scope)
    first = block(config, vid)
    second = block(config, vid, "maintenance_archive")
    with patch.object(index, "update", side_effect=AssertionError("index must not run")):
        for _ in range(3):
            assert maintenance.next_task(config, {vid}, time.time() + 4000) is None
    row = store.library(config, vid)
    assert row["index_attempts"] == 4 and row["retries"] == 0
    assert row["error"] == "placement_reconciliation_required"
    assert note_search_jobs.read(config, vid)["reason"] == "placement_reconciliation_required"
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (first,))
    assert maintenance.next_task(config, {vid}, time.time()) is None
    assert [
        r["id"] for r in presentation.index_status(config, vid)["sync"]["dependency"]["operations"]
    ] == [second]
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (second,))
    assert maintenance.next_task(config, {vid}, time.time()) == (vid, cid)
    row = store.library(config, vid)
    assert row["index_after"] == row["index_attempts"] == 0 and row["error"] is None


def test_readonly_old_ledger_reports_dependency_without_schema_write(config, scope):
    _, vid, _ = scope
    dirty(config, scope)
    identity = block(config, vid, "maintenance_archive")
    path = config.state_root / store.NAME
    before = path.read_bytes()
    value = presentation.index_status(config, vid)
    assert value["state"] == "blocked" and value["sync"]["next_retry"] is None
    with database.connect(config.state_root, readonly=True) as db:
        activity = note_index_activity.snapshot(config, db)
        deps = operation_dependencies.pending(db, vid)
    assert any(v["state"] == "blocked" for v in activity["vaults"] if v["vault_id"] == vid)
    assert deps[0]["id"] == identity
    assert path.read_bytes() == before
    with store.connection(config) as side:
        assert (
            side.execute("SELECT 1 FROM sqlite_master WHERE name='index_dependencies'").fetchone()
            is None
        )


def test_worker_race_keeps_specific_dependency_without_failure_backoff(config, scope):
    client, vid, _ = scope
    cid = client["id"]
    dirty(config, scope)
    original = index.visibility.read
    fired = False

    def racing(*args, **kwargs):
        nonlocal fired
        result = original(*args, **kwargs)
        if not fired:
            fired = True
            block(config, vid)
        return result

    with patch.object(index.visibility, "read", racing):
        with pytest.raises(IntegrityError, match="placement_reconciliation_required") as caught:
            index.update(config, cid, vid)
    maintenance.finished(config, vid, failed=True, error=caught.value)
    row = store.library(config, vid)
    assert row["retries"] == 0 and row["index_attempts"] == 4
    assert row["error"] == "placement_reconciliation_required"
    assert presentation.index_status(config, vid)["state"] == "blocked"


def test_io_failure_retains_existing_backoff_contract(config, scope):
    _, vid, _ = scope
    dirty(config, scope)
    maintenance.finished(config, vid, failed=True, error=OSError("synthetic"), now=100)
    row = store.library(config, vid)
    assert row["index_attempts"] == 5 and row["retries"] == 1 and row["index_after"] == 1900


def test_dependency_release_preserves_a_new_io_error_cooldown(config, scope):
    _, vid, _ = scope
    dirty(config, scope)
    identity = block(config, vid)
    assert maintenance.next_task(config, {vid}, time.time() + 4000) is None
    future = time.time() + 1800
    with store.connection(config, write=True) as side:
        side.execute(
            "UPDATE libraries SET error='vault_sync_cloud_timeout',"
            "index_after=?,index_attempts=7 WHERE id=?",
            (future, vid),
        )
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (identity,))
    assert maintenance.next_task(config, {vid}, time.time()) is None
    row = store.library(config, vid)
    assert row["error"] == "vault_sync_cloud_timeout"
    assert row["index_after"] == future and row["index_attempts"] == 7


def test_broken_owner_ledger_never_hides_the_blocking_operation(config, scope):
    _, vid, _ = scope
    dirty(config, scope)
    identity = block(config, vid)
    from fiber_bundle.organization import store as organization

    organization.put(config, "jobs", "synthetic-broken-job", vid, {"items": []})
    with organization.connection(config, write=True) as db:
        db.execute("UPDATE jobs SET value='invalid json' WHERE id='synthetic-broken-job'")
    value = presentation.index_status(config, vid)
    assert value["state"] == "blocked"
    dependency = value["sync"]["dependency"]
    assert dependency["owner_lookup_failed"] is True
    assert dependency["operations"][0]["id"] == identity
    assert dependency["recovery"] == "review_original_operation"
    with database.connect(config.state_root, readonly=True) as db:
        activity = note_index_activity.snapshot(config, db)
    assert next(v for v in activity["vaults"] if v["vault_id"] == vid)["state"] == "blocked"


def test_catalog_and_graph_http_errors_prioritize_live_operation_dependencies(config, scope, http):
    client, vid, _ = scope
    dirty(config, scope)
    identity = block(config, vid)
    from fiber_bundle.organization import store as organization

    organization.put(
        config,
        "jobs",
        "synthetic-owner",
        vid,
        {"id": "synthetic-owner", "items": [{"archive_operation": identity}]},
    )
    for path in ["/admin/organization", "/admin/knowledge-graph/notes"]:
        response = http.get(path, params={"client_id": client["id"], "vault_id": vid})
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["error"] == "placement_reconciliation_required"
        assert body["dependency"]["operations"][0]["id"] == identity
        assert body["dependency"]["operations"][0]["job_ids"] == ["synthetic-owner"]


def test_worker_dependency_removed_before_finished_wakes_without_a_cooldown(config, scope):
    client, vid, _ = scope
    dirty(config, scope)
    identity = block(config, vid)
    with pytest.raises(IntegrityError, match="placement_reconciliation_required") as caught:
        index.update(config, client["id"], vid)
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vault_operations SET state='completed' WHERE id=?", (identity,))
    maintenance.finished(config, vid, failed=True, error=caught.value)
    row = store.library(config, vid)
    assert row["index_after"] == row["index_attempts"] == row["retries"] == 0
    assert row["error"] is None
    assert maintenance.next_task(config, {vid}, time.time()) == (vid, client["id"])
