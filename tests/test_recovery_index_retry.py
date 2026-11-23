"""Committed recovery can queue a new source job while retaining an old index cooldown."""

import time
from unittest.mock import patch

import pytest
from test_explicit_index_refresh import drain
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_vault_sync_index import build
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, note_search_jobs
from fiber_bundle.organization import package_discovery
from fiber_bundle.vault_sync import commands, explicit_refresh, maintenance, scanner, store


def recovered_source(config, vid):
    maintenance.finished(config, vid, failed=True, error=OSError("synthetic index failure"))
    with database.locked(config):
        note_search_jobs.changed(config, vid)
    job = note_search_jobs.read(config, vid)
    assert job["state"] == "pending" and job["reason"] == "source_changed"
    assert store.library(config, vid)["index_after"] > time.time()
    return job


@pytest.mark.parametrize("metadata_observed", [False, True])
def test_one_gui_click_retries_committed_recovery_despite_its_new_pending_job(
    config, identity_scope, metadata_observed
):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    (root / "recovered.md").write_text("# A newly recovered source\n")
    source_job = recovered_source(config, vid)
    if metadata_observed:
        # The earlier GUI could already finish reobservation and clear the error
        # without releasing the failed index attempt's separate cooldown.
        store.enqueue(config, vid, [("", 1)], "20")
        drain(config)
        assert store.library(config, vid)["error"] is None
    attempts = store.library(config, vid)["index_attempts"]
    with patch.object(scanner, "apply", side_effect=AssertionError("HTTP metadata scan")):
        retry = commands.submit(config, client["id"], vid, reobserve=True)
        marker = explicit_refresh.read(config, vid)
        queued = store.due(config, now=time.time() + 100)
        repeated = commands.submit(config, client["id"], vid, reobserve=True)
    assert retry["state"] == "pending" and not retry["coalesced"]
    assert retry["job_id"] != source_job["id"]
    assert note_search_jobs.read(config, vid)["reason"] == "explicit_update"
    assert repeated["coalesced"] and repeated["job_id"] == retry["job_id"]
    assert explicit_refresh.read(config, vid) == marker
    assert store.due(config, now=time.time() + 100) == queued
    assert store.library(config, vid)["index_after"] == 0
    assert store.library(config, vid)["index_attempts"] == attempts
    drain(config)
    assert (
        maintenance.next_task(config, {vid}, time.time()) is None
    )  # Package barrier stays active.
    package_discovery.tick(config)
    assert maintenance.next_task(config, {vid}, time.time()) == (vid, client["id"])
    assert build(config, identity_scope)["state"] == "ready"


def test_background_source_queue_does_not_consume_the_explicit_recovery_retry(
    config, identity_scope
):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    source_job = recovered_source(config, vid)
    before = store.library(config, vid)
    with patch.object(package_discovery, "request", side_effect=AssertionError("package request")):
        queued = commands.submit(config, client["id"], vid)
    assert queued["coalesced"] and queued["job_id"] == source_job["id"]
    assert note_search_jobs.read(config, vid) == source_job
    assert store.library(config, vid) == before
    assert explicit_refresh.read(config, vid) is None


def test_synchronous_recovery_refresh_does_not_reobserve_or_discover_packages(
    config, identity_scope
):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    recovered_source(config, vid)
    before = store.status(config, vid)
    with (
        patch.object(scanner, "apply", side_effect=AssertionError("metadata scan")),
        patch.object(package_discovery, "request", side_effect=AssertionError("package request")),
        patch("fiber_bundle.note_index.read_note", side_effect=AssertionError("unchanged body")),
    ):
        result = commands.refresh(config, client["id"], vid)
    assert result["state"] == "ready" and result["reused_notes"] == 1
    assert explicit_refresh.read(config, vid) is None
    assert store.status(config, vid)["scans"] == before["scans"]
    assert store.status(config, vid)["bodies"] == before["bodies"]


@pytest.mark.parametrize("stage", ["prepared", "observation", "packages"])
def test_recovery_retry_keeps_existing_intent_owner_and_package_token(
    config, identity_scope, stage
):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    source_job = recovered_source(config, vid)
    with database.locked(config), database.connect(config.state_root) as db:
        if stage == "prepared":
            with patch.object(explicit_refresh, "_queue", side_effect=OSError("power loss")):
                with pytest.raises(OSError):
                    explicit_refresh.request(config, db, client["id"], vid, source_job["id"])
        else:
            explicit_refresh.request(config, db, client["id"], vid, source_job["id"])
    if stage == "packages":
        drain(config)
    marker = explicit_refresh.read(config, vid)
    with database.connect(config.state_root) as db:
        peer = auth.create_client(db, "query-only recovery retry", False)
        auth.set_grant(db, peer["id"], vid, query=True, original=False, add=False)
    retry = commands.submit(config, peer["id"], vid, reobserve=True)
    current = explicit_refresh.read(config, vid)
    assert retry["job_id"] == source_job["id"] and not retry["coalesced"]
    assert note_search_jobs.read(config, vid)["reason"] == "explicit_update"
    assert current["client_id"] == client["id"] and current["packages"]
    assert current["requested_at"] == marker["requested_at"]
    if stage != "prepared":
        assert current == marker
    assert package_discovery.status(config, client["id"], vid)["request_id"] == source_job["id"]
    assert store.library(config, vid)["index_after"] == 0
    assert commands.submit(config, peer["id"], vid, reobserve=True)["coalesced"]
    assert maintenance.next_task(config, {vid}, time.time()) is None


@pytest.mark.parametrize(
    "state,reason,attempts,index_delay",
    [
        ("building", "source_changed", 1, 900),
        ("pending", "explicit_update", 1, 900),
        ("pending", "source_changed", 0, 900),
        ("pending", "source_changed", 1, -1),
    ],
)
def test_active_or_unfailed_pending_requests_keep_their_new_cooldowns(
    config, identity_scope, state, reason, attempts, index_delay
):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    job_id = note_search_jobs.record(config, vid, state, reason=reason)
    after = time.time() + 900
    with store.connection(config, write=True) as side:
        side.execute(
            "UPDATE libraries SET state='waiting',error='vault_sync_source_unavailable',"
            "index_attempts=?,index_after=? WHERE id=?",
            (attempts, time.time() + index_delay, vid),
        )
        revision = side.execute(
            "SELECT revision FROM files WHERE vault=? AND path='manual.md'", (vid,)
        ).fetchone()[0]
        side.execute(
            "INSERT INTO issues VALUES(?,?,?,?,?,?)",
            (vid, "manual.md", "vault_sync_cloud_metadata_unavailable", revision, after, 2),
        )
    before = store.library(config, vid), note_search_jobs.read(config, vid)
    assert commands.submit(config, client["id"], vid, reobserve=True)["coalesced"]
    assert commands.submit(config, client["id"], vid, reobserve=True)["job_id"] == job_id
    assert note_search_jobs.read(config, vid) == before[1]
    current = store.library(config, vid)
    assert all(current[key] == before[0][key] for key in ("index_after", "index_attempts", "error"))
    with store.connection(config) as side:
        assert (
            side.execute("SELECT next_check FROM issues WHERE vault=?", (vid,)).fetchone()[0]
            == after
        )
