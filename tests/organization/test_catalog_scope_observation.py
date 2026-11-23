"""Catalog preflight binds only the selected mode, with live namespace proofs."""

import os
from unittest.mock import patch

import pytest
from test_catalog_directory_metadata import baseline
from test_jobs import ready as ready

from fiber_bundle import database, note_index, note_inventory
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import catalog, catalog_inventory
from fiber_bundle.vault_sync import store


def snapshot(config, ready, quick=True):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        return catalog.snapshot(
            config, db, cid, vid, partial=True, include=["Inbox"] if quick else None
        )


@pytest.mark.parametrize(
    "path", ["Resource/unrelated.md", "Resource/new-folder", "Resource/irrelevant.png", "Outside"]
)
def test_quick_ignores_outside_event_but_full_keeps_relevant_events(config, ready, path):
    _, vid, root, *_ = ready
    baseline(config, vid, root)
    store.enqueue(config, vid, [(path, 0)], "17")
    entities, mocs, _ = snapshot(config, ready)
    assert len(entities) == len(mocs) == 1
    with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_pending"):
        snapshot(config, ready, quick=False)


@pytest.mark.parametrize("path", ["Inbox/传热.md", "Map/热管理 MOC.md", "", "Inbox"])
def test_quick_keeps_related_and_unknown_directory_events(config, ready, path):
    _, vid, root, *_ = ready
    baseline(config, vid, root)
    store.enqueue(config, vid, [(path, 1 if not path else 0)], "17")
    with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_pending"):
        snapshot(config, ready)


def test_known_attachment_edit_and_unknown_regular_attachment_do_not_block(config, ready):
    _, vid, root, *_ = ready
    attachment = root / "Inbox/image.png"
    attachment.write_bytes(b"old")
    baseline(config, vid, root)
    attachment.write_bytes(b"new")
    (root / "Inbox/other.png").write_bytes(b"new")
    store.enqueue(config, vid, [("Inbox/image.png", 2), ("Inbox/other.png", 16)], "17")
    assert len(snapshot(config, ready)[0]) == 1


def test_ancestor_unrelated_name_changes_are_proven_but_relevant_names_are_rejected(config, ready):
    _, vid, root, *_ = ready
    baseline(config, vid, root)
    (root / "Unrelated").mkdir()
    (root / "Unrelated/never-read.md").write_text("private")
    assert len(snapshot(config, ready)[0]) == 1
    (root / "Inbox/new.md").write_text("# new")
    with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_changed"):
        snapshot(config, ready)


@pytest.mark.parametrize("related", [False, True])
def test_event_arriving_while_inventory_is_read_is_rechecked(config, ready, related):
    _, vid, root, *_ = ready
    baseline(config, vid, root)
    original = note_inventory.read

    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        store.enqueue(config, vid, [("Inbox/传热.md" if related else "Resource/x.md", 2)], "17")
        return value

    with patch.object(note_inventory, "read", side_effect=read):
        if related:
            with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_changed"):
                snapshot(config, ready)
        else:
            assert len(snapshot(config, ready)[0]) == 1


def test_filtered_namespace_never_stats_protected_child(tmp_path):
    (tmp_path / "Inbox").mkdir()
    private = tmp_path / "protected_project"
    private.mkdir()
    from fiber_bundle import vault_scope
    from fiber_bundle.vault_sync import scanner

    files = {
        p: {"directory": 1, "signature": scanner.signature((tmp_path / p).stat())}
        for p in ("", "Inbox")
    }
    (private / "secret.md").write_text("private")
    # Force one root namespace proof; excluded names must be filtered before is_dir/stat.
    os.utime(tmp_path)
    original = os.scandir

    def scan(path):
        assert private not in (path, *path.parents)
        return original(path)

    with patch.object(catalog_inventory.os, "scandir", scan):
        catalog_inventory._directories(
            tmp_path, files, vault_scope.Scope(include=("Inbox",)), lambda _: False, {}
        )


def test_newly_unavailable_note_retains_explicit_partial_coverage(config, ready):
    _, vid, root, _, note = ready
    baseline(config, vid, root)
    original = note_index.read_note

    def read(root, path, maximum, **kwargs):
        if path == note.relative_to(root).as_posix():
            raise IntegrityError("cloud_item_not_downloaded")
        return original(root, path, maximum, **kwargs)

    with patch.object(note_index, "read_note", side_effect=read):
        entities, mocs, context = snapshot(config, ready)
    assert not entities and len(mocs) == 1
    assert not context["coverage"]["complete"]
    assert context["coverage"]["unavailable_count"] == 1


def test_scope_tightening_during_catalog_read_cannot_return_old_entities(config, ready):
    _, vid, root, *_ = ready
    baseline(config, vid, root)
    original = note_inventory.read

    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        with database.connect(config.state_root) as db:
            db.execute(
                "UPDATE vaults SET scope_json=? WHERE id=?",
                ('{"include":[""],"exclude":["Inbox"]}', vid),
            )
        return value

    with patch.object(note_inventory, "read", side_effect=read):
        with pytest.raises(catalog_inventory.Unavailable, match="organization_catalog_changed"):
            snapshot(config, ready)
