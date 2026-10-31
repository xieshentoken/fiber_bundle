"""Isolated external-save, partial coverage and delivery-race contracts."""

import json
import os
import time
from unittest.mock import patch

import pytest
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope
from test_note_identities import track
from test_vault_sync_reconcile import changed, observe

from fiber_bundle import database, note_search_cache, search
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_sync import index, scanner, store


def build(config, fixture):
    client, vid, _ = fixture
    scanner.heartbeat(config, vid)
    return index.update(config, client["id"], vid)


def query(config, fixture, text="sentinel"):
    with database.connect(config.state_root) as db:
        return search.search(config, db, fixture[0]["id"], text, [fixture[1]])


def test_save_updates_only_changed_body_and_never_notifies_organization(config, identity_scope):
    client, vid, root = identity_scope
    track(config, identity_scope)
    (root / "other.md").write_text("unchanged sentinel")
    observe(config, identity_scope)
    with patch("fiber_bundle.organization.jobs.notify", side_effect=AssertionError("MOC trigger")):
        build(config, identity_scope)
        before = store.status(config, vid)["bodies"]
        (root / "manual.md").write_text("edited sentinel")
        changed(config, vid)
        result = build(config, identity_scope)
        assert result["updated_notes"] == 1
        assert store.status(config, vid)["bodies"] == before + 1
    with patch.object(note_search_cache, "fingerprint", side_effect=AssertionError("Vault scan")):
        assert len(query(config, identity_scope)["results"]) == 2
        assert query(config, identity_scope, "edited")["coverage_complete"]


def test_atomic_save_rebinds_before_index_projection(config, identity_scope):
    registered = track(config, identity_scope)
    client, vid, root = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    temporary = root / ".replacement"
    temporary.write_text("replacement sentinel")
    os.replace(temporary, root / "manual.md")
    changed(config, vid, flags=4 | 2)
    build(config, identity_scope)
    result = query(config, identity_scope, "replacement")
    assert result["results"][0]["note_id"] == registered["note_identity"]


def test_dirty_note_does_not_block_other_hits_or_claim_complete_miss(config, identity_scope):
    client, vid, root = identity_scope
    (root / "other.md").write_text("stable sentinel")
    observe(config, identity_scope)
    build(config, identity_scope)
    (root / "manual.md").write_text("new topic")
    store.enqueue(config, vid, [("manual.md", 2)], "100")
    with patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("full fallback")):
        result = query(config, identity_scope)
        assert [h["note_path"] for h in result["results"]] == ["other.md"]
        assert result["coverage_complete"] is False
        with pytest.raises(note_search_cache.IndexUnavailable):
            query(config, identity_scope, "missing keyword")


def test_ambiguous_replacement_is_isolated(config, identity_scope):
    track(config, identity_scope)
    client, vid, root = identity_scope
    (root / "other.md").write_text("stable sentinel")
    observe(config, identity_scope)
    build(config, identity_scope)
    replacement = root / ".replacement"
    replacement.write_text("different sentinel")
    os.replace(replacement, root / "manual.md")
    changed(config, vid, flags=1)
    result = build(config, identity_scope)
    assert result["needs_attention"] == 1
    result = query(config, identity_scope)
    assert [h["note_path"] for h in result["results"]] == ["other.md"]
    assert not result["coverage_complete"]


def test_query_exit_rechecks_only_delivered_files(config, identity_scope):
    from fiber_bundle.search_snapshot import SearchSnapshot

    client, vid, root = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="changed before delivery"):
            with SearchSnapshot(config, db, client["id"]) as snapshot:
                search.search(config, db, client["id"], "sentinel", [vid], snapshot=snapshot)
                (root / "manual.md").write_text("changed after reading")


def test_incremental_slices_and_restart_do_not_re_read_completed_notes(config, identity_scope):
    _, vid, root = identity_scope
    for i in range(57):
        (root / f"note-{i}.md").write_text(f"topic sentinel {i}")
    observe(config, identity_scope)
    first = build(config, identity_scope)
    assert first["pending_notes"] == 8
    second = build(config, identity_scope)
    assert second["updated_notes"] == 8
    assert store.status(config, vid)["bodies"] == 58
    assert build(config, identity_scope)["updated_notes"] == 0


def test_failed_index_retry_is_durable_and_bounded(config, identity_scope):
    from fiber_bundle.vault_sync import maintenance

    client, vid, _ = identity_scope
    observe(config, identity_scope)
    now = time.time()
    assert maintenance.next_task(config, {vid}, now) == (vid, client["id"])
    maintenance.finished(config, vid, failed=True, now=now)
    assert maintenance.next_task(config, {vid}, now + 59) is None
    assert maintenance.next_task(config, {vid}, now + 60) is not None


def test_legacy_cache_is_readmitted_once_before_reuse(config, identity_scope):
    import sqlite3

    _, vid, _ = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    with sqlite3.connect(note_search_cache.location(config, vid)) as old:
        old.execute("DROP TABLE sync_metadata")
    before = store.status(config, vid)["bodies"]
    assert build(config, identity_scope)["updated_notes"] == 1
    assert store.status(config, vid)["bodies"] == before + 1
    assert build(config, identity_scope)["updated_notes"] == 0


def test_new_scope_denial_prunes_before_any_denied_file_access(config, identity_scope):
    from pathlib import Path

    _, vid, root = identity_scope
    (root / "private").mkdir()
    (root / "private/secret.md").write_text("sensitive sentinel")
    observe(config, identity_scope)
    build(config, identity_scope)
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["private"]}), vid),
        )
    original = Path.lstat

    def allowed_stat(path):
        assert not str(path).startswith(str(root / "private")), "denied metadata access"
        return original(path)

    with patch.object(Path, "lstat", allowed_stat):
        with pytest.raises(note_search_cache.IndexUnavailable):
            build(config, identity_scope)
        while tasks := store.due(config, now=time.time() + 10):
            for task in tasks:
                scanner.apply(config, task)
        assert build(config, identity_scope)["updated_notes"] == 1
        assert len(query(config, identity_scope)["results"]) == 1
    with store.connection(config) as side:
        assert not side.execute("SELECT 1 FROM files WHERE path LIKE 'private%' ").fetchone()


def test_missing_local_index_is_never_displayed_as_ready(config, identity_scope):
    from fiber_bundle.vault_sync import presentation

    _, vid, _ = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    note_search_cache.location(config, vid).unlink()
    assert presentation.index_status(config, vid)["state"] == "stale"
    with pytest.raises(note_search_cache.IndexUnavailable):
        query(config, identity_scope)
    assert store.library(config, vid)["state"] == "index_pending"
    assert build(config, identity_scope)["updated_notes"] == 1


def test_unavailable_note_has_persistent_backoff_and_other_notes_stay_searchable(
    config, identity_scope
):
    from fiber_bundle.vault_sync import reconcile

    _, vid, root = identity_scope
    (root / "other.md").write_text("stable sentinel")
    observe(config, identity_scope)
    original = reconcile.current

    def local_note(config, client, identity, path):
        if path == "manual.md":
            raise IntegrityError("cloud_item_pending_stale_or_conflicted")
        return original(config, client, identity, path)

    with patch.object(reconcile, "current", side_effect=local_note):
        for attempt, delay in enumerate(store.DELAYS, 1):
            now = time.time()
            build(config, identity_scope)
            with store.connection(config, write=True) as db:
                issue = dict(db.execute("SELECT * FROM issues WHERE vault=?", (vid,)).fetchone())
                assert issue["attempts"] == attempt
                assert now + delay <= issue["next_check"] <= time.time() + delay
                db.execute("UPDATE issues SET next_check=0 WHERE vault=?", (vid,))
    result = query(config, identity_scope)
    assert [r["note_path"] for r in result["results"]] == ["other.md"]
    assert result["coverage_complete"] is False


def test_registration_invalidates_only_its_path_not_completed_slices(config, identity_scope):
    client, vid, root = identity_scope
    (root / "other.md").write_text("stable sentinel")
    observe(config, identity_scope)
    build(config, identity_scope)
    before = store.status(config, vid)["bodies"]
    registered = track(config, identity_scope)
    result = build(config, identity_scope)
    assert result["updated_notes"] == 1 and result["reused_notes"] == 1
    assert store.status(config, vid)["bodies"] == before + 1
    result = query(config, identity_scope)
    assert len(result["results"]) == 2
    assert (
        next(n for n in result["results"] if n["note_path"] == "manual.md")["note_id"]
        == registered["note_identity"]
    )


def test_visibility_change_excludes_all_affected_old_rows_before_partial_publication(
    config, identity_scope
):
    from fiber_bundle.vault_sync import visibility

    _, vid, root = identity_scope
    for i in range(55):
        (root / f"note-{i}.md").write_text("stable sentinel")
    observe(config, identity_scope)
    while build(config, identity_scope)["pending_notes"]:
        pass
    real_read = visibility.read

    def hidden_context(*args):
        proof = real_read(*args)
        return visibility.Proof("new-context", proof.paths)

    with patch.object(visibility, "read", side_effect=hidden_context):
        result = build(config, identity_scope)
        assert result["pending_notes"] == 6
        with note_search_cache.connection(config, vid) as db:
            assert db.execute("SELECT count(*) FROM notes").fetchone()[0] == 50


def test_cloud_reason_partial_status_and_manual_retry_do_not_scan_other_files(
    config, identity_scope
):
    from fiber_bundle.vault_sync import commands, presentation, reconcile

    client, vid, root = identity_scope
    (root / "other.md").write_text("stable sentinel")
    observe(config, identity_scope)
    original = reconcile.current

    def missing(config, client, identity, path):
        if path == "manual.md":
            raise IntegrityError("cloud_metadata_unavailable")
        return original(config, client, identity, path)

    with patch.object(reconcile, "current", side_effect=missing):
        assert build(config, identity_scope)["state"] == "partial"
    with patch.object(scanner, "apply", side_effect=AssertionError("Vault scan")):
        status = presentation.index_status(config, vid)
        assert status["state"] == "partial" and not status["coverage_complete"]
        assert status["sync"]["issue_groups"]["cloud"] == 1
        assert status["sync"]["issues"][0]["reason"] == "vault_sync_cloud_metadata_unavailable"
        assert status["sync"]["next_retry"] > time.time()
        request = commands.submit(config, client["id"], vid)
        assert commands.submit(config, client["id"], vid)["job_id"] == request["job_id"]
        # Only the formerly unavailable note is read again.
        before = store.status(config, vid)["bodies"]
        assert build(config, identity_scope)["updated_notes"] == 1
        assert store.status(config, vid)["bodies"] == before + 1
        assert presentation.index_status(config, vid)["state"] == "ready"


def test_capacity_failure_reports_precise_reason_and_preserves_last_index(config, identity_scope):
    import sqlite3

    from fiber_bundle.vault_sync import maintenance, presentation

    _, vid, _ = identity_scope
    observe(config, identity_scope)
    build(config, identity_scope)
    before = note_search_cache.location(config, vid).read_bytes()
    error = sqlite3.OperationalError("sensitive internal context")
    error.sqlite_errorcode = sqlite3.SQLITE_FULL
    maintenance.finished(config, vid, failed=True, error=error)
    status = presentation.index_status(config, vid)
    assert status["state"] == "failed"
    assert status["sync"]["error"] == "vault_sync_index_capacity"
    assert "sensitive" not in json.dumps(status)
    assert note_search_cache.location(config, vid).read_bytes() == before
