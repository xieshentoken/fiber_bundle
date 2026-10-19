from unittest.mock import patch

import pytest
from test_article_publication import completed, destination
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate

from fiber_bundle import (
    article_publication,
    auth,
    database,
    note_inventory,
    note_placement_execution,
    publication_locations,
    vault_layout_migration,
)
from fiber_bundle.config import IntegrityError, InvalidInput


def legacy(config, policy, scope):
    rid = completed(config, policy, scope)
    result = article_publication.publish(config, scope[0], rid)
    with database.connect(config.state_root) as db:
        auth.set_grant(db, *scope[:2], query=True, original=True, add=True)
    return rid, result, destination(config, result)


def test_selected_conversion_keeps_source_human_text_and_single_index_body(
    config, policy, article_scope
):
    rid, result, note = legacy(config, policy, article_scope)
    note.write_text(note.read_text() + "\n人工补充\n")
    original = note.read_bytes()
    source = note.parent
    root = source.parent
    (root / "Unmanaged").mkdir()
    (root / "Unmanaged/Untouched.md").write_text("private test")
    (root / "Incoming.md").write_text("[source](" + result["note_path"] + ")\n")
    activate(config, article_scope[1])
    with database.connect(config.state_root) as db:
        assert len(vault_layout_migration.candidates(config, db, *article_scope[:2])) == 1
        packet = vault_layout_migration.preview(
            config, db, *article_scope[:2], "article", result["publication_id"]
        )
        assert packet["ready"] and packet["modified_notes"] == ["note.md"]
        assert publication_locations.get(db, "article", result["publication_id"]) is None
        assert note.read_bytes() == original
        assert any(
            n.relative_path == result["note_path"]
            for n in note_inventory.read(
                config, db, auth.grant(db, *article_scope[:2], "query")
            ).notes
        )
        applied = vault_layout_migration.apply(
            config, db, *article_scope[:2], packet["operation_id"]
        )
        assert applied["backup_complete"] and applied["state"] == "completed"
        assert note.read_bytes() == original
        location = publication_locations.get(db, "article", result["publication_id"])
        new = root / location["entry_note_path"]
        assert "人工补充" in new.read_text() and new.parent.name == "Inbox"
        notes = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query")).notes
        matches = [n for n in notes if n.publication_id == result["publication_id"]]
        assert len(matches) == 1 and matches[0].relative_path == location["entry_note_path"]
        assert "Inbox/" in (root / "Incoming.md").read_text()
        assert (root / "Unmanaged/Untouched.md").read_text() == "private test"
        assert not vault_layout_migration.candidates(config, db, *article_scope[:2])
        assert (
            article_publication.publish_locked(config, db, article_scope[0], rid)["note_path"]
            == location["entry_note_path"]
        )
        assert (
            vault_layout_migration.apply(config, db, *article_scope[:2], packet["operation_id"])[
                "state"
            ]
            == "completed"
        )


def test_preview_whitelist_and_changes_block_before_copy(config, policy, article_scope):
    _, result, note = legacy(config, policy, article_scope)
    activate(config, article_scope[1])
    with database.connect(config.state_root) as db:
        with pytest.raises(InvalidInput):
            vault_layout_migration.preview(config, db, *article_scope[:2], "article", "unmanaged")
        packet = vault_layout_migration.preview(
            config, db, *article_scope[:2], "article", result["publication_id"]
        )
        note.write_text(note.read_text() + "external")
        with pytest.raises(IntegrityError, match="source_changed"):
            vault_layout_migration.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert publication_locations.get(db, "article", result["publication_id"]) is None


def test_interrupted_conversion_keeps_both_and_explicit_reconcile(config, policy, article_scope):
    _, result, note = legacy(config, policy, article_scope)
    activate(config, article_scope[1])
    with database.connect(config.state_root) as db:
        packet = vault_layout_migration.preview(
            config, db, *article_scope[:2], "article", result["publication_id"]
        )
        original = note.read_bytes()
        with (
            patch.object(note_placement_execution, "complete", side_effect=OSError("injected")),
            pytest.raises(OSError),
        ):
            vault_layout_migration.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert note.read_bytes() == original
        with pytest.raises(IntegrityError, match="reconciliation"):
            note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        assert (
            vault_layout_migration.apply(
                config, db, *article_scope[:2], packet["operation_id"], reconcile=True
            )["state"]
            == "completed"
        )


def test_converted_backup_restores_old_and_current_body_with_source_and_moc(
    config, policy, article_scope, tmp_path
):
    from uuid import uuid4

    from fiber_bundle import backup_bundle, backup_restore, moc_store, note_provenance
    from fiber_bundle.config import load_config

    _, published, note = legacy(config, policy, article_scope)
    original = note.read_bytes()
    root_key = note.parent.parent.stat()
    identity, mid = "publication:" + published["publication_id"], str(uuid4())
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record(
            "Synthetic MOC", root_key.st_dev, root_key.st_ino, 1, {identity: ["manual"]}
        ),
        "create",
        0,
    )
    activate(config, article_scope[1])
    with database.connect(config.state_root) as db:
        preview = vault_layout_migration.preview(
            config, db, *article_scope[:2], "article", published["publication_id"]
        )
        converted = vault_layout_migration.apply(
            config, db, *article_scope[:2], preview["operation_id"]
        )
    backup = config.state_root / "layout-migration-backups" / preview["operation_id"]
    assert (backup / "source-files/note.md").read_bytes() == original
    assert (backup / "metadata.sqlite3").is_file()
    bundle, destination = tmp_path / "encrypted", tmp_path / "restored"
    backup_bundle.create(config, bundle, "synthetic-converted-backup-only")
    backup_restore.restore(bundle, destination, "synthetic-converted-backup-only")
    restored = load_config(destination / "config.test.json", destination)
    backup_restore.activate(restored, source_stopped=True)
    assert (
        restored.state_root / backup.relative_to(config.state_root) / "source-files/note.md"
    ).read_bytes() == original
    assert moc_store.load(restored, article_scope[1], mid).members == {identity: ["manual"]}
    with database.connect(restored.state_root) as db:
        db.execute("UPDATE clients SET active=1 WHERE id=?", (article_scope[0],))
        grant = auth.grant(db, *article_scope[:2], "query")
        inventory = note_inventory.read(restored, db, grant)
        current = [n for n in inventory.notes if n.publication_id == published["publication_id"]]
        assert len(current) == 1 and current[0].relative_path == converted["target_path"]
        provenance = note_provenance.describe(
            restored, db, article_scope[0], grant, inventory, current[0]
        )
        assert (
            provenance["originals"]
            and provenance["source_version_id"] == published["source_version_id"]
        )


@pytest.mark.parametrize("attachment_state", ["detached", "trashed"])
def test_restoring_excluded_layout_preserves_metadata_without_old_vault_access(
    config, policy, article_scope, tmp_path, attachment_state
):
    import json
    import sqlite3
    from uuid import uuid4

    from fiber_bundle import backup_bundle, backup_restore, moc_store, vault_catalog

    _, published, note = legacy(config, policy, article_scope)
    activate(config, article_scope[1])
    root = note.parent.parent
    mid, identity = str(uuid4()), "publication:" + published["publication_id"]
    key = root.stat()
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record("Historical MOC", key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
        "create",
        0,
    )
    with database.connect(config.state_root) as db:
        preview = vault_layout_migration.preview(
            config, db, *article_scope[:2], "article", published["publication_id"]
        )
        vault_layout_migration.apply(config, db, *article_scope[:2], preview["operation_id"])
        vault_catalog.unlink(config, db, article_scope[1])
        if attachment_state == "trashed":
            db.execute(
                "UPDATE vaults SET attachment_state='trashed' WHERE id=?", (article_scope[1],)
            )
    backup_bundle.create(config, tmp_path / "bundle", "synthetic-excluded-layout-only")
    root.rename(root.with_name("outside-backup-ownership"))
    result = backup_restore.restore(
        tmp_path / "bundle", tmp_path / "restored", "synthetic-excluded-layout-only"
    )
    assert result["state"] == "restored_locked" and not result["automatic_start"]
    state = tmp_path / "restored/.state"
    with sqlite3.connect(state / "metadata.sqlite3") as db:
        assert (
            db.execute(
                "SELECT state FROM publication_locations WHERE publication_id=?",
                (published["publication_id"],),
            ).fetchone()[0]
            == "reconcile"
        )
    with sqlite3.connect(state / "moc-associations.sqlite3") as db:
        record = json.loads(
            db.execute("SELECT record_json FROM mocs WHERE id=?", (mid,)).fetchone()[0]
        )
        assert record["members"] == {identity: ["manual"]}
        assert (record["vault_dev"], record["vault_ino"]) == (0, 0)
