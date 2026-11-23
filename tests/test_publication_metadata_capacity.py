"""Attachment-heavy Vault repair retains publication, source and link safety."""

from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published
from test_vault_metadata_inventory import files

from fiber_bundle import (
    auth,
    database,
    moc_store,
    note_identity_rebind,
    note_inventory,
    note_placement,
    note_provenance,
    publication_locations,
    publication_rebind,
    publication_reconcile_scan,
    vault_scope,
)
from fiber_bundle.config import IntegrityError


def history(db):
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {
        name: [tuple(row) for row in db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
        for name in (
            "article_publications",
            "source_versions",
            "version_assets",
            "sources",
            "assets",
            "receipts",
            "model_calls",
            "model_configuration_calls",
        )
        if name in names
    }


def atomic_edit(note):
    temporary = note.with_suffix(".replacement")
    temporary.write_bytes(note.read_bytes() + b"\nHuman edit retained.\n")
    temporary.replace(note)
    return note.read_bytes()


def test_large_attachment_vault_rebind_keeps_identity_source_history_and_archive_links(
    config, policy, article_scope
):
    _, result, note = published(
        config, policy, article_scope, policy_version=2, image_dir="Attachments/images"
    )
    root = note.parent.parent
    identity = "publication:" + result["publication_id"]
    mid = str(uuid4())
    key = root.stat()
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record("Manual MOC", key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
        "create",
        0,
    )
    (root / "Incoming.md").write_text("[source](<" + result["note_path"] + ">)\n")
    files(root / "Attachments/extra", 10001)
    current = atomic_edit(note)
    with database.connect(config.state_root) as db:
        original_history = history(db)
        original_location = publication_locations.get(db, "article", result["publication_id"])
        original_hashes = original_location["plan"]["published_hashes"].copy()
        before_scan = db.total_changes
        scan = publication_reconcile_scan.scan(config, db, *article_scope[:2])
        assert db.total_changes == before_scan
        assert len(scan["items"]) == 1 and scan["model_calls"] == 0
        item = scan["items"][0]
        assert item["publication_id"] == result["publication_id"]
        assert item["suggested_path"] == result["note_path"]
        packet = publication_rebind.preview(
            config,
            db,
            *article_scope[:2],
            "article",
            result["publication_id"],
            1,
            result["note_path"],
        )
        assert packet["modified_notes"] == [result["note_path"]]
        applied = publication_rebind.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert applied["state"] == "completed" and not applied["vault_write_performed"]
        grant = auth.grant(db, *article_scope[:2], "query")
        inventory = note_inventory.read(config, db, grant)
        repaired = next(item for item in inventory.notes if item.identity == identity)
        assert repaired.relative_path == result["note_path"]
        provenance = note_provenance.describe(
            config, db, article_scope[0], grant, inventory, repaired
        )
        assert provenance["source_kind"] == "user_revision" and provenance["originals"]
        assert history(db) == original_history
        location = publication_locations.get(db, "article", result["publication_id"])
        assert location["revision"] == 2
        assert location["plan"]["published_hashes"] == original_hashes
        assert moc_store.load(config, article_scope[1], mid).members == {identity: ["manual"]}
        updates, unresolved = note_placement.plan_links(
            root,
            vault_scope.for_vault(root, grant),
            inventory,
            {result["note_path"]: "Archive/Repaired.md"},
        )
        assert not unresolved
        assert "Incoming.md" in {item["path"] for item in updates}
        assert history(db) == original_history
    assert note.read_bytes() == current
    assert not (root / "Archive/Repaired.md").exists()
    assert result["note_path"] in (root / "Incoming.md").read_text()


def test_bulk_scan_full_ordinary_context_counts_nonhidden_attachments_above_old_limit(
    config, policy, article_scope
):
    _, result, note = published(config, policy, article_scope)
    files(note.parent.parent / "images/extra", 10001)
    current = atomic_edit(note)
    with database.connect(config.state_root) as db:
        changes = db.total_changes
        scan = publication_reconcile_scan.scan(config, db, *article_scope[:2])
        assert db.total_changes == changes
        assert [item["publication_id"] for item in scan["items"]] == [result["publication_id"]]
    assert note.read_bytes() == current


def test_oversized_metadata_never_creates_partial_rebind_or_link_plan(
    config, policy, article_scope
):
    _, result, note = published(config, policy, article_scope)
    root = note.parent.parent
    with database.connect(config.state_root) as db:
        grant = auth.grant(db, *article_scope[:2], "query")
        inventory = note_inventory.read(config, db, grant)
        original_history = history(db)
        original_location = publication_locations.get(db, "article", result["publication_id"])
        operations = [tuple(row) for row in db.execute("SELECT * FROM vault_operations")]
        # Legacy image directories are deliberately not hidden from the ordinary snapshot.
        files(root / "images/extra", 20001)
        current = atomic_edit(note)
        for action in (
            lambda: note_identity_rebind.context(config, db, *article_scope[:2]),
            lambda: publication_reconcile_scan.scan(config, db, *article_scope[:2]),
            lambda: publication_rebind.preview(
                config,
                db,
                *article_scope[:2],
                "article",
                result["publication_id"],
                1,
                result["note_path"],
            ),
            lambda: note_placement.plan_links(
                root,
                vault_scope.for_vault(root, grant),
                inventory,
                {result["note_path"]: "Archive/Unavailable.md"},
            ),
        ):
            with pytest.raises(IntegrityError, match="^layout_metadata_inventory_limit$"):
                action()
            assert [
                tuple(row) for row in db.execute("SELECT * FROM vault_operations")
            ] == operations
            assert (
                publication_locations.get(db, "article", result["publication_id"])
                == original_location
            )
            assert history(db) == original_history
    assert note.read_bytes() == current
    assert not (root / "Archive/Unavailable.md").exists()


@pytest.mark.parametrize(
    "contents,error",
    [
        (b"x" * (1024 * 1024 + 1), "note is not a bounded independent regular file"),
        (b"\xff", "note encoding unsupported"),
    ],
)
def test_ordinary_context_preserves_non_capacity_integrity_errors(
    config, policy, article_scope, contents, error
):
    _, _, note = published(config, policy, article_scope)
    (note.parent.parent / "manual.md").write_bytes(contents)
    with database.connect(config.state_root) as db:
        changes = db.total_changes
        with pytest.raises(IntegrityError, match="^" + error + "$"):
            note_identity_rebind.context(config, db, *article_scope[:2])
        assert db.total_changes == changes
