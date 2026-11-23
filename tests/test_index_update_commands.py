"""The manual button and background worker use one durable, metadata-only request."""

from threading import Event
from unittest.mock import patch

import pytest
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_search_cache import indexed_scope as indexed_scope
from test_vault_sync_index import build, query
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, note_search_build, note_search_cache, note_search_jobs
from fiber_bundle.config import InvalidInput
from fiber_bundle.vault_sync import commands, index, maintenance, scanner


def test_managed_build_keeps_sync_proof_and_never_notifies(config, identity_scope):
    observe(config, identity_scope)
    build(config, identity_scope)
    with patch("fiber_bundle.organization.jobs.notify", side_effect=AssertionError("MOC")):
        result = note_search_build.build(config, identity_scope[0]["id"], identity_scope[1])
    assert result["state"] == "ready" and result["reused_notes"] == 1
    assert query(config, identity_scope)["results"]
    with note_search_cache.connection(config, identity_scope[1]) as db:
        assert db.execute("SELECT * FROM sync_metadata").fetchone()


def test_requests_coalesce_and_restart_resumes_remaining_slices(config, identity_scope):
    client, vid, root = identity_scope
    for i in range(57):
        (root / f"{i}.md").write_text("sentinel")
    observe(config, identity_scope)
    first = commands.submit(config, client["id"], vid)
    assert commands.submit(config, client["id"], vid)["job_id"] == first["job_id"]
    assert maintenance.next_task(config, {vid}, 100000000000) == (vid, client["id"])
    result = build(config, identity_scope)
    assert result["state"] == "building"
    assert note_search_jobs.read(config, vid)["id"] == first["job_id"]
    assert commands.submit(config, client["id"], vid)["coalesced"] is True
    # Re-read all persisted state, as a new backend instance would.
    assert maintenance.next_task(config, {vid}, 100000000000) == (vid, client["id"])
    assert build(config, identity_scope)["state"] == "ready"
    assert note_search_jobs.read(config, vid)["id"] == first["job_id"]


def test_cancel_before_publish_preserves_index(config, identity_scope):
    observe(config, identity_scope)
    build(config, identity_scope)
    path = note_search_cache.location(config, identity_scope[1])
    before = path.read_bytes()
    stop = Event()
    stop.set()
    with pytest.raises(InvalidInput, match="cancelled"):
        index.update(config, identity_scope[0]["id"], identity_scope[1], stop)
    assert path.read_bytes() == before


def test_submit_rechecks_permission(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vid, query=False, original=False, add=True)
    with pytest.raises(auth.Denied):
        commands.submit(config, client["id"], vid)
    assert note_search_jobs.read(config, vid) is None


def test_http_update_accepts_without_reading_bodies_or_duplicate_work(http, config, indexed_scope):
    cid, _, vid, root = indexed_scope
    observe(config, ({"id": cid}, vid, root))
    scanner.heartbeat(config, vid)
    with patch("fiber_bundle.note_index.read_note", side_effect=AssertionError("body I/O")):
        one = http.post("/admin/note-index/update", json={"client_id": cid, "vault_id": vid})
        two = http.post("/admin/note-index/update", json={"client_id": cid, "vault_id": vid})
    assert one.status_code == two.status_code == 202
    assert one.json()["job_id"] == two.json()["job_id"]
    assert one.json()["state"] == "pending" and two.json()["coalesced"]


def test_legacy_build_does_not_notify_organization(http, config, indexed_scope):
    cid, _, vid, _ = indexed_scope
    with patch("fiber_bundle.organization.jobs.notify", side_effect=AssertionError("MOC")):
        assert note_search_build.build(config, cid, vid)["state"] == "ready"


def test_orphan_building_marker_is_requeued_without_losing_task_identity(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    task = note_search_jobs.record(config, vid, "building", reason="source_changed")
    queued = commands.submit(config, client["id"], vid)
    assert queued["job_id"] == task
    assert maintenance.next_task(config, {vid}, 100000000000) == (vid, client["id"])
    assert build(config, identity_scope)["state"] == "ready"
