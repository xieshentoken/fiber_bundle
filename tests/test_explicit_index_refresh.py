"""Explicit refresh reconciles metadata; it never rewrites a user's notes or identities."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track
from test_vault_sync_reconcile import observe

from fiber_bundle import auth, database, note_identity_store
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import package_discovery
from fiber_bundle.vault_sync import commands, explicit_refresh, index, reconcile, scanner, store


def drain(config):
    for _ in range(100):
        tasks = store.due(config, now=time.time() + 100)
        if not tasks:
            return
        for task in tasks:
            scanner.apply(config, task)
    raise AssertionError("metadata queue did not converge")


def test_gui_refresh_repairs_hidden_child_directory_drift_and_coalesces(config, identity_scope):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    before = (root / "manual.md").read_bytes()
    stored = None
    with store.connection(config) as side:
        stored = side.execute(
            "SELECT signature FROM files WHERE vault=? AND path=''", (vid,)
        ).fetchone()[0]
    (root / ".editor-scratch").write_text("hidden temporary state")
    assert scanner.signature(root.lstat()) != stored
    with patch.object(scanner, "apply", side_effect=AssertionError("HTTP metadata traversal")):
        first = commands.submit(config, client["id"], vid, reobserve=True)
        pending = store.due(config, now=time.time() + 100)
        marker = explicit_refresh.read(config, vid)
        again = commands.submit(config, client["id"], vid, reobserve=True)
        assert again["job_id"] == first["job_id"] and again["coalesced"]
        assert store.due(config, now=time.time() + 100) == pending
        assert explicit_refresh.read(config, vid) == marker
    assert explicit_refresh.activity(config, vid)["stage"] == "observation"
    with patch("fiber_bundle.note_index.read_note", side_effect=AssertionError("body read")):
        drain(config)
    with store.connection(config) as side:
        assert side.execute(
            "SELECT signature FROM files WHERE vault=? AND path=''", (vid,)
        ).fetchone()[0] == scanner.signature(root.lstat())
        assert not side.execute("SELECT 1 FROM files WHERE path LIKE '%scratch%'").fetchone()
    assert (root / "manual.md").read_bytes() == before
    assert store.status(config, vid)["bodies"] == 0
    assert not explicit_refresh.ready(config, vid)  # Package stage has not completed.
    package_discovery.tick(config)
    assert explicit_refresh.ready(config, vid)


def test_query_only_refresh_keeps_package_admission_permission_separate(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vid, query=True, original=False, add=False)
    commands.submit(config, client["id"], vid, reobserve=True)
    drain(config)
    assert not explicit_refresh.read(config, vid)["packages"]
    assert explicit_refresh.ready(config, vid)


def test_coalesced_client_cannot_drop_existing_package_readiness_barrier(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    request = commands.submit(config, client["id"], vid, reobserve=True)
    marker = explicit_refresh.read(config, vid)
    with database.connect(config.state_root) as db:
        peer = auth.create_client(db, "query-only index peer", False)
        auth.set_grant(db, peer["id"], vid, query=True, original=False, add=False)
    repeated = commands.submit(config, peer["id"], vid, reobserve=True)
    assert repeated["job_id"] == request["job_id"]
    assert explicit_refresh.read(config, vid) == marker
    assert marker["packages"] and not explicit_refresh.ready(config, vid)


def test_observed_atomic_editor_save_survives_broad_refresh(config, identity_scope):
    client, vid, root = identity_scope
    registered = track(config, identity_scope)
    observe(config, identity_scope)
    (root / ".save").write_text("a new local revision")
    os.replace(root / ".save", root / "manual.md")
    # The explicit root slice runs before the individual delayed rename event.
    store.enqueue(config, vid, [("manual.md", 4 | 2)], "123", now=time.time() + 10)
    commands.submit(config, client["id"], vid, reobserve=True)
    root_task = next(task for task in store.due(config, now=time.time() + 100) if not task["path"])
    scanner.apply(config, root_task)
    assert (
        reconcile.current(config, client["id"], vid, "manual.md").identity
        == registered["note_identity"]
    )


def test_ambiguous_replacement_is_not_adopted_by_explicit_refresh(config, identity_scope):
    client, vid, root = identity_scope
    track(config, identity_scope)
    observe(config, identity_scope)
    original = note_identity_store.records(config, vid)
    (root / ".save").write_text("unrelated copied document")
    os.replace(root / ".save", root / "manual.md")
    commands.submit(config, client["id"], vid, reobserve=True)
    drain(config)
    with pytest.raises(IntegrityError, match="vault_sync_replacement_needs_review"):
        reconcile.current(config, client["id"], vid, "manual.md")
    assert note_identity_store.records(config, vid) == original


def test_protected_descendants_are_filtered_before_any_child_probe(config, identity_scope):
    client, vid, root = identity_scope
    private = root / "protected"
    private.mkdir()
    (private / "do-not-touch.md").write_text("not authorized")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            ('{"include":[""],"exclude":["protected"]}', vid),
        )
    observe(config, identity_scope)
    commands.submit(config, client["id"], vid, reobserve=True)
    real = Path.lstat

    def checked(path, *args, **kwargs):
        assert path != private and private not in path.parents
        return real(path, *args, **kwargs)

    with patch.object(Path, "lstat", checked):
        drain(config)
    with store.connection(config) as side:
        assert not side.execute("SELECT 1 FROM files WHERE path LIKE 'protected%'").fetchone()


def test_prepared_refresh_resumes_queue_only_after_authorization(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    with patch.object(explicit_refresh, "_queue", side_effect=RuntimeError("power loss")):
        with pytest.raises(RuntimeError, match="power loss"):
            commands.submit(config, client["id"], vid, reobserve=True)
    assert explicit_refresh.read(config, vid)["phase"] == "prepared"
    assert not explicit_refresh.ready(config, vid)
    explicit_refresh.advance(config, vid)
    assert explicit_refresh.read(config, vid)["phase"] == "queued"
    before = store.due(config, now=time.time() + 100)
    explicit_refresh.advance(config, vid)
    assert store.due(config, now=time.time() + 100) == before


def test_prepared_refresh_revocation_stops_only_its_dispatch(config, identity_scope):
    client, vid, _ = identity_scope
    observe(config, identity_scope)
    with patch.object(explicit_refresh, "_queue", side_effect=RuntimeError("power loss")):
        with pytest.raises(RuntimeError):
            commands.submit(config, client["id"], vid, reobserve=True)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, client["id"], vid, query=False, original=False, add=False)
    explicit_refresh.advance(config, vid)
    assert explicit_refresh.read(config, vid)["phase"] == "blocked"
    assert explicit_refresh.activity(config, vid)["state"] == "waiting"
    assert not store.due(config, now=time.time() + 100)


def test_in_place_obsidian_edit_is_indexed_without_rewriting_source_or_model(
    config, identity_scope
):
    client, vid, root = identity_scope
    registered = track(config, identity_scope)
    observe(config, identity_scope)
    index.update(config, client["id"], vid)
    path = root / "manual.md"
    path.write_text("# My Obsidian edit\n\nNew reference topic.\n")
    before = path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns
    with patch(
        "fiber_bundle.organization.jobs.notify", side_effect=AssertionError("model trigger")
    ):
        commands.submit(config, client["id"], vid, reobserve=True)
        drain(config)
        package_discovery.tick(config)
        result = index.update(config, client["id"], vid)
    assert result["updated_notes"] == 1
    current = note_identity_store.records(config, vid)[0]
    assert current.identity == registered["note_identity"] and current.revision == 2
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == before


def test_changed_scope_during_metadata_slice_cannot_publish_old_authorized_children(
    config, identity_scope
):
    client, vid, root = identity_scope
    observe(config, identity_scope)
    path = root / "newly-private.md"
    path.write_text("not in the final authorized scope")
    commands.submit(config, client["id"], vid, reobserve=True)
    task = next(t for t in store.due(config, now=time.time() + 100) if not t["path"])
    original = scanner.scope_guard.current
    calls = 0

    def scope_changed(config, row):
        nonlocal calls
        calls += 1
        if calls == 2:
            with database.connect(config.state_root) as db:
                db.execute(
                    "UPDATE vaults SET scope_json=? WHERE id=?",
                    ('{"include":[""],"exclude":["newly-private.md"]}', vid),
                )
        return original(config, row)

    with patch.object(scanner.scope_guard, "current", side_effect=scope_changed):
        with pytest.raises(IntegrityError, match="vault_sync_scope_changed"):
            scanner.apply(config, task)
    with store.connection(config) as side:
        assert not side.execute("SELECT 1 FROM files WHERE path='newly-private.md'").fetchone()
