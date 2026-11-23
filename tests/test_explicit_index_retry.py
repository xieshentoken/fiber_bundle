"""An explicit retry releases one failed attempt without bypassing its source barriers."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, get_ident
from unittest.mock import patch

import pytest
from test_explicit_index_refresh import drain
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_vault_sync_index import build
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, note_search_cache, note_search_jobs
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import package_discovery
from fiber_bundle.vault_sync import (
    commands,
    explicit_refresh,
    index,
    maintenance,
    presentation,
    reconcile,
    scanner,
    store,
)


def failed_index(config, vid):
    """Persist the long-lived retry state that an observer failure leaves behind."""
    with store.connection(config, write=True) as side:
        side.execute("UPDATE libraries SET index_attempts=17 WHERE id=?", (vid,))
    maintenance.finished(config, vid, failed=True, error=OSError("synthetic index failure"))
    return note_search_jobs.read(config, vid)


def test_gui_retry_releases_cooldowns_once_and_restart_publishes_real_index(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    commands.submit(config, client["id"], vid, reobserve=True)
    drain(config)
    package_discovery.tick(config)
    build(config, identity_scope)
    completed_refresh = explicit_refresh.read(config, vid)
    (root / "fresh.md").write_text("new source discovered by the explicit retry")
    with store.connection(config, write=True) as side:
        revision = side.execute(
            "SELECT revision FROM files WHERE vault=? AND path='manual.md'", (vid,)
        ).fetchone()[0]
        side.execute(
            "INSERT INTO issues VALUES(?,?,?,?,?,?)",
            (
                vid,
                "manual.md",
                "vault_sync_cloud_metadata_unavailable",
                revision,
                time.time() + 900,
                9,
            ),
        )
    old = failed_index(config, vid)
    assert maintenance.next_task(config, {vid}, time.time()) is None
    before = store.status(config, vid)
    with (
        patch("fiber_bundle.note_index.read_note", side_effect=AssertionError("HTTP body read")),
        patch.object(scanner, "apply", side_effect=AssertionError("HTTP metadata scan")),
        patch.object(package_discovery, "scan", side_effect=AssertionError("HTTP package scan")),
    ):
        retry = commands.submit(config, client["id"], vid, reobserve=True)
        pending = store.due(config, now=time.time() + 100)
        marker = explicit_refresh.read(config, vid)
        again = commands.submit(config, client["id"], vid, reobserve=True)
    assert retry["state"] == "pending" and not retry["coalesced"]
    assert retry["job_id"] != old["id"]
    assert again["job_id"] == retry["job_id"] and again["coalesced"]
    assert marker["job_id"] != completed_refresh["job_id"]
    assert store.due(config, now=time.time() + 100) == pending
    assert explicit_refresh.read(config, vid) == marker
    with store.connection(config) as side:
        issue = side.execute("SELECT * FROM issues WHERE vault=?", (vid,)).fetchone()
        assert issue["next_check"] == 0 and issue["attempts"] == 9
    status = store.status(config, vid)
    assert status["index_after"] == 0 and status["index_attempts"] == 18
    assert status["error"] is None and status["bodies"] == before["bodies"]
    assert not presentation.index_status(config, vid)["coverage_complete"]
    # A fresh config instance resumes only persisted work, without another click.
    restarted = replace(config)
    with patch("fiber_bundle.note_index.read_note", side_effect=AssertionError("scan body read")):
        drain(restarted)
    assert maintenance.next_task(restarted, {vid}, time.time()) is None  # Package barrier.
    with patch("fiber_bundle.organization.jobs.notify", side_effect=AssertionError("model work")):
        package_discovery.tick(restarted)
        assert maintenance.next_task(restarted, {vid}, time.time()) == (vid, client["id"])
        result = build(restarted, identity_scope)
    assert result["state"] == "ready" and result["updated_notes"] == 2
    assert store.status(config, vid)["bodies"] == before["bodies"] + 2
    assert note_search_jobs.read(config, vid)["id"] == retry["job_id"]
    assert presentation.index_status(config, vid)["coverage_complete"]


def test_retry_failure_keeps_last_index_and_resumes_normal_backoff(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    before = note_search_cache.location(config, vid).read_bytes()
    (root / "manual.md").write_text("changed while the old index is retained")
    failed_index(config, vid)
    retry = commands.submit(config, client["id"], vid, reobserve=True)
    drain(config)
    package_discovery.tick(config)
    error = sqlite3.OperationalError("synthetic capacity failure")
    error.sqlite_errorcode = sqlite3.SQLITE_FULL
    with patch.object(index.index_storage, "reclaim", side_effect=error):
        with pytest.raises(sqlite3.OperationalError):
            build(config, identity_scope)
    now = time.time()
    maintenance.finished(config, vid, failed=True, now=now, error=error)
    assert note_search_cache.location(config, vid).read_bytes() == before
    assert note_search_jobs.read(config, vid)["state"] == "waiting"
    assert note_search_jobs.read(config, vid)["id"] == retry["job_id"]
    persisted = store.library(config, vid)
    assert persisted["index_attempts"] == 19
    assert persisted["index_after"] == now + store.DELAYS[-1]
    assert maintenance.next_task(replace(config), {vid}, now) is None
    assert store.library(config, vid) == persisted
    status = presentation.index_status(config, vid)
    assert status["state"] == "failed" and not status["coverage_complete"]


@pytest.mark.parametrize("recovered_pending", [False, True])
def test_repeated_clicks_during_slices_preserve_new_per_note_failure_backoff(
    config, identity_scope, recovered_pending
):
    client, vid, root = identity_scope
    for number in range(55):
        (root / f"note-{number}.md").write_text("unchanged sentinel")
    observe(config, identity_scope)
    failed_index(config, vid)
    if recovered_pending:
        note_search_jobs.changed(config, vid)
    retry = commands.submit(config, client["id"], vid, reobserve=recovered_pending)
    if recovered_pending:
        drain(config)
        package_discovery.tick(config)
    original = reconcile.current

    def unavailable(config, client, identity, path):
        if path == "manual.md":
            raise IntegrityError("cloud_metadata_unavailable")
        return original(config, client, identity, path)

    with patch.object(reconcile, "current", side_effect=unavailable):
        assert build(config, identity_scope)["state"] == "building"
    with store.connection(config) as side:
        issue = dict(side.execute("SELECT * FROM issues WHERE vault=?", (vid,)).fetchone())
    assert issue["next_check"] > time.time()
    assert commands.submit(config, client["id"], vid)["coalesced"]
    assert commands.submit(config, client["id"], vid)["job_id"] == retry["job_id"]
    with store.connection(config) as side:
        assert dict(side.execute("SELECT * FROM issues WHERE vault=?", (vid,)).fetchone()) == issue

    def unchanged_failure(config, client, identity, path):
        assert path != "manual.md", "another click erased the failed note's cooldown"
        return original(config, client, identity, path)

    with patch.object(reconcile, "current", side_effect=unchanged_failure):
        result = build(config, identity_scope)
    assert result["state"] == "partial" and result["needs_attention"] == 1
    assert result["updated_notes"] == 6
    assert not presentation.index_status(config, vid)["coverage_complete"]


def test_retry_rechecks_permission_before_changing_failed_state(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    failed_index(config, vid)
    before = store.library(config, vid), note_search_jobs.read(config, vid)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vid, query=False, original=False, add=True)
    with pytest.raises(auth.Denied):
        commands.submit(config, client["id"], vid, reobserve=True)
    assert (store.library(config, vid), note_search_jobs.read(config, vid)) == before
    assert explicit_refresh.read(config, vid) is None


def test_synchronous_retry_does_not_start_metadata_or_package_discovery(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    failed_index(config, vid)
    before = store.status(config, vid)
    with (
        patch.object(scanner, "apply", side_effect=AssertionError("extra scan")),
        patch.object(package_discovery, "request", side_effect=AssertionError("package request")),
        patch("fiber_bundle.note_index.read_note", side_effect=AssertionError("unchanged body")),
    ):
        result = commands.refresh(config, client["id"], vid)
    assert result["state"] == "ready" and result["reused_notes"] == 1
    assert explicit_refresh.read(config, vid) is None
    assert store.status(config, vid)["scans"] == before["scans"]
    assert store.status(config, vid)["bodies"] == before["bodies"]


@pytest.mark.parametrize("stage", ["prepared", "observation", "packages"])
def test_query_only_retry_preserves_unfinished_owner_and_package_barrier(
    config, identity_scope, stage
):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    if stage == "prepared":
        with patch.object(explicit_refresh, "_queue", side_effect=OSError("power loss")):
            with pytest.raises(OSError):
                commands.submit(config, client["id"], vid, reobserve=True)
        request_id = note_search_jobs.read(config, vid)["id"]
    else:
        request_id = commands.submit(config, client["id"], vid, reobserve=True)["job_id"]
    if stage == "packages":
        drain(config)
    marker = explicit_refresh.read(config, vid)
    failed_index(config, vid)  # An older worker can finish while the new barrier is pending.
    with database.connect(config.state_root) as db:
        peer = auth.create_client(db, "query-only retry peer", False)
        auth.set_grant(db, peer["id"], vid, query=True, original=False, add=False)
    with (
        patch.object(scanner, "apply", side_effect=AssertionError("duplicate scan")),
        patch.object(
            package_discovery, "scan", side_effect=AssertionError("duplicate package scan")
        ),
    ):
        retry = commands.submit(config, peer["id"], vid, reobserve=True)
        queued = store.due(config, now=time.time() + 100)
        again = commands.submit(config, peer["id"], vid, reobserve=True)
    assert retry["state"] == "pending" and not retry["coalesced"]
    assert retry["job_id"] == request_id == again["job_id"] and again["coalesced"]
    current = explicit_refresh.read(config, vid)
    assert current["client_id"] == client["id"] and current["packages"]
    assert current["requested_at"] == marker["requested_at"]
    if stage != "prepared":
        assert current == marker
    assert package_discovery.status(config, client["id"], vid)["request_id"] == request_id
    assert store.due(config, now=time.time() + 100) == queued
    restarted = replace(config)
    assert maintenance.next_task(restarted, {vid}, time.time()) is None
    with pytest.raises(note_search_cache.IndexUnavailable):
        index.update(restarted, peer["id"], vid)
    drain(restarted)
    assert not explicit_refresh.ready(restarted, vid)
    package_discovery.tick(restarted)
    task = maintenance.next_task(restarted, {vid}, time.time())
    assert task is not None and task[0] == vid and task[1] in {client["id"], peer["id"]}
    assert build(restarted, identity_scope)["state"] == "ready"


def test_failed_state_and_retry_are_serialized_under_one_writer_lease(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    first = commands.submit(config, client["id"], vid)
    written, release, clicking, clicked = Event(), Event(), Event(), Event()
    real_connection = store.connection
    writer = None

    @contextmanager
    def pause_after_side_commit(config, *, write=False):
        with real_connection(config, write=write) as side:
            yield side
        if write and get_ident() == writer:
            written.set()
            assert release.wait(5)

    def fail():
        nonlocal writer
        writer = get_ident()
        maintenance.finished(config, vid, failed=True)

    def click():
        clicking.set()
        try:
            return commands.submit(config, client["id"], vid)
        finally:
            clicked.set()

    with patch.object(store, "connection", pause_after_side_commit), ThreadPoolExecutor(2) as pool:
        failure = pool.submit(fail)
        assert written.wait(5)
        request = pool.submit(click)
        try:
            assert clicking.wait(5)
            assert not clicked.wait(0.1), "retry saw only half of the failed attempt"
        finally:
            release.set()
        failure.result(timeout=5)
        retry = request.result(timeout=5)
    assert retry["state"] == "pending" and not retry["coalesced"]
    assert retry["job_id"] != first["job_id"]
    assert store.library(config, vid)["index_after"] == 0
    assert maintenance.next_task(config, {vid}, time.time()) == (vid, client["id"])


def test_pending_retry_cas_cannot_replace_a_newer_request(config, identity_scope):
    _, vid, _ = identity_scope
    first = note_search_jobs.record(config, vid, "waiting", reason="index_update_failed")
    newer = note_search_jobs.record(config, vid, "pending", reason="explicit_update")
    before = note_search_jobs.read(config, vid)
    assert (
        note_search_jobs.record(config, vid, "pending", reason="explicit_update", expected_id=first)
        == newer
    )
    assert note_search_jobs.read(config, vid) == before
