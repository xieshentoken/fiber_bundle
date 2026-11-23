"""UI and backup detectors consume the same local catalogue without Vault traversal."""

import time
from unittest.mock import patch

import pytest

from fiber_bundle import database, note_search_cache, vault_scope
from fiber_bundle.backup_automation import changes
from fiber_bundle.config import InvalidInput
from fiber_bundle.vault_sync import scanner
from fiber_bundle.vault_sync import store as sync


def register_all(config):
    with database.connect(config.state_root, readonly=True) as db:
        rows = db.execute("SELECT * FROM vaults").fetchall()
    for row in rows:
        root = config.vault_path(row["relative_path"])
        sync.register(config, row["id"], str(root), vault_scope.Scope(), row["id"])
    while tasks := sync.due(config, now=time.time() + 100):
        for task in tasks:
            scanner.apply(config, task)
    for row in rows:
        scanner.heartbeat(config, row["id"])
    return rows


def test_backup_observation_matches_legacy_without_rescanning(config, store):
    root = config.vault_path(dict(config.vaults)["personal"])
    (root / "note.md").write_text("sample")
    before = changes.observe(config)
    register_all(config)
    with patch.object(changes, "entries", side_effect=AssertionError("Vault traversal")):
        after = changes.observe(config)
        assert changes.observe(config) == after
    assert {k: v for k, v in before["items"].items() if k.startswith("vault:")} == {
        k: v for k, v in after["items"].items() if k.startswith("vault:")
    }


def test_index_status_does_not_compute_filesystem_fingerprint(config, store):
    rows = register_all(config)
    with (
        database.connect(config.state_root) as db,
        patch.object(note_search_cache, "fingerprint", side_effect=AssertionError("GUI scan")),
    ):
        for _ in range(20):
            value = note_search_cache.status(config, db, rows[0])
            assert value["state"] == "pending" and value["sync"]["baseline"]


def test_backup_waits_for_event_processing_without_falling_back_to_scan(config, store):
    rows = register_all(config)
    sync.enqueue(config, rows[0]["id"], [("note.md", 0)], "10")
    with patch.object(changes, "entries", side_effect=AssertionError("fallback scan")):
        with pytest.raises(InvalidInput, match="backup_vault_sync_pending"):
            changes.observe(config)


def test_new_packaged_library_does_not_trigger_legacy_backup_scan(config, store, monkeypatch):
    monkeypatch.setenv("FIBER_EVENTS_HELPER", "/not-started-yet")
    with patch.object(changes, "entries", side_effect=AssertionError("bootstrap scan")):
        with pytest.raises(InvalidInput, match="backup_vault_sync_pending"):
            changes.observe(config)
