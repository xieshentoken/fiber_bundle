"""Metadata-only directory drift is proven narrowly, never treated as complete blindly."""

import os
from unittest.mock import patch

import pytest
from test_jobs import ready as ready

from fiber_bundle import database, vault_scope
from fiber_bundle.organization import catalog, catalog_inventory
from fiber_bundle.vault_sync import scanner, store


def baseline(config, vid, root):
    store.register(config, vid, str(root), vault_scope.Scope(), "test")
    while tasks := store.due(config, now=10):
        for task in tasks:
            scanner.apply(config, task, now=10)
    scanner.heartbeat(config, vid)


def touch_metadata(path):
    before = path.stat()
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_ctime_ns != before.st_ctime_ns


def test_metadata_only_drift_reads_current_notes_without_scanning_other_directories(config, ready):
    cid, vid, root, moc, note = ready
    baseline(config, vid, root)
    before = {p: p.read_bytes() for p in (moc, note)}
    touch_metadata(root / "Inbox")
    scanned = []
    original = os.scandir

    def scan(path):
        scanned.append(path)
        return original(path)

    with (
        database.connect(config.state_root) as db,
        patch.object(catalog_inventory.os, "scandir", scan),
    ):
        entities, mocs, context = catalog.snapshot(config, db, cid, vid, partial=True)
    assert len(entities) == 1 and len(mocs) == 1
    assert scanned == [root / "Inbox"]
    assert all(p.read_bytes() == b for p, b in before.items())
    assert not context.get("coverage", {}).get("unavailable_count")


def test_changed_names_with_restored_mtime_are_not_mistaken_for_metadata_only(config, ready):
    cid, vid, root, _, note = ready
    baseline(config, vid, root)
    before = (root / "Inbox").stat()
    note.rename(root / "Inbox/更名.md")
    os.utime(root / "Inbox", ns=(before.st_atime_ns, before.st_mtime_ns))
    with (
        database.connect(config.state_root) as db,
        pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_changed"),
    ):
        catalog.snapshot(config, db, cid, vid, partial=True)
    assert (root / "Inbox/更名.md").read_text().endswith("傅里叶定律。\n")


def test_permission_change_does_not_trigger_name_fallback(config, ready):
    cid, vid, root, *_ = ready
    baseline(config, vid, root)
    folder = root / "Inbox"
    folder.chmod((folder.stat().st_mode & 0o777) ^ 0o040)
    with (
        database.connect(config.state_root) as db,
        patch.object(
            catalog_inventory.os,
            "scandir",
            side_effect=AssertionError("Must fail before enumeration"),
        ),
    ):
        with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_changed"):
            catalog.snapshot(config, db, cid, vid, partial=True)


def test_excluded_directory_is_never_traversed_and_second_drift_is_bounded(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    excluded = tmp_path / "private"
    excluded.mkdir()
    (excluded / "private.md").write_text("Do not read")
    scope = vault_scope.Scope(exclude=("private",))
    files = {
        "": {"directory": 1, "signature": scanner.signature(tmp_path.stat())},
        "allowed": {"directory": 1, "signature": scanner.signature(allowed.stat())},
    }
    touch_metadata(tmp_path)
    checks = {}
    catalog_inventory._directories(tmp_path, files, scope, lambda _: False, checks)
    assert list(checks) == [""]
    touch_metadata(allowed)
    with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_changed"):
        catalog_inventory._directories(tmp_path, files, scope, lambda _: False, checks)
