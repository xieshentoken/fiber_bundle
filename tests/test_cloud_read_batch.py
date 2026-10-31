"""Current cloud batching in GUI inventories retains independent source and boundary checks."""

import hashlib
import subprocess
from unittest.mock import patch

import pytest
from test_cloud_directory_status import native as native

from fiber_bundle import cloud_files, note_index, vault_scope
from fiber_bundle.config import IntegrityError


def cloud_root(tmp_path, monkeypatch, native):
    helper = native / "helper"
    monkeypatch.setenv("FIBER_CLOUD_HELPER", str(helper))
    monkeypatch.setenv("FIBER_CLOUD_HELPER_SHA256", hashlib.sha256(helper.read_bytes()).hexdigest())
    root = tmp_path / "Mobile Documents/vault"
    (root / "Inbox").mkdir(parents=True)
    for number in range(20):
        (root / f"Inbox/note-{number:02d}.md").write_text(f"# Synthetic {number}\nbody\n")
    protected = root / "01-Projects/protected-project"
    protected.mkdir(parents=True)
    (protected / "not-downloaded.icloud").mkdir()
    return root, vault_scope.Scope(exclude=("01-Projects/protected-project",))


def snapshot(root, scope):
    return note_index.snapshot(root, set(), set(), scope=scope)


def test_twenty_bodies_use_two_native_checks_and_next_snapshot_is_fresh(
    tmp_path, monkeypatch, native
):
    root, scope = cloud_root(tmp_path, monkeypatch, native)
    with patch.object(cloud_files.subprocess, "run", wraps=subprocess.run) as run:
        notes = snapshot(root, scope)
    assert len(notes) == 20 and run.call_count == 2  # Before all bodies, after all bodies.
    path = root / "Inbox/note-00.md"
    path.write_text("# New source\nchanged\n")
    assert (
        next(n for n in snapshot(root, scope) if n.relative_path.endswith("00.md")).text
        == path.read_text()
    )
    assert all("protected-project" not in n.relative_path for n in notes)


def test_change_after_body_read_blocks_whole_inventory(tmp_path, monkeypatch, native):
    root, scope = cloud_root(tmp_path, monkeypatch, native)
    read = note_index.read_note
    seen = []

    def changed(*args, **kwargs):
        note = read(*args, **kwargs)
        seen.append(note)
        if len(seen) == 2:
            (root / seen[0].relative_path).write_text("# Changed after the read\n")
        return note

    monkeypatch.setattr(note_index, "read_note", changed)
    with pytest.raises(IntegrityError, match="cloud_item_pending_stale_or_conflicted"):
        snapshot(root, scope)


def test_status_change_at_final_check_never_delivers_partial_inventory(
    tmp_path, monkeypatch, native
):
    root, scope = cloud_root(tmp_path, monkeypatch, native)
    check = cloud_files.require_available_many
    calls = []

    def stale(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise IntegrityError("cloud_item_pending_stale_or_conflicted")
        return check(*args, **kwargs)

    monkeypatch.setattr(cloud_files, "require_available_many", stale)
    with pytest.raises(IntegrityError):
        snapshot(root, scope)
    assert len(calls) == 2


def test_explicit_partial_inventory_isolates_failed_batch_member(tmp_path, monkeypatch, native):
    root, scope = cloud_root(tmp_path, monkeypatch, native)
    check = cloud_files.require_available_many

    def unavailable(parent, names):
        if "note-03.md" in names:
            raise IntegrityError("cloud_item_not_downloaded")
        return check(parent, names)

    monkeypatch.setattr(cloud_files, "require_available_many", unavailable)
    errors = {}
    notes = note_index.snapshot(root, set(), set(), scope=scope, note_errors=errors)
    assert len(notes) == 19
    assert errors == {"Inbox/note-03.md": "vault_sync_cloud_pending"}
    assert all(n.relative_path != "Inbox/note-03.md" for n in notes)
