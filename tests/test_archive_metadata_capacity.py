"""Archive execution uses the same bounded metadata inventory as its preview."""

import json
from urllib.parse import unquote
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published
from test_publication_metadata_capacity import history
from test_vault_metadata_inventory import files

from fiber_bundle import (
    auth,
    database,
    moc_placement_projection,
    moc_store,
    note_inventory,
    note_placement,
    note_placement_execution,
    publication_locations,
    vault_scope,
)
from fiber_bundle.config import IntegrityError
from fiber_bundle.objects import digest


def archive_source(config, policy, scope, *, attachments=10001):
    _, result, note = published(
        config,
        policy,
        scope,
        policy_version=2,
        image_dir="Attachments/images",
        image_link_style="markdown",
    )
    root = note.parent.parent
    files(root / "Attachments/extra", attachments)
    (root / "Incoming.md").write_text("[incoming](<" + result["note_path"] + ">)\n")
    identity = "publication:" + result["publication_id"]
    mid = str(uuid4())
    key = root.stat()
    moc_store.save(
        config,
        scope[1],
        mid,
        scope[0],
        moc_store.Record("Existing manual MOC", key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
        "create",
        0,
    )
    return result, note, root, identity, mid


@pytest.mark.parametrize("interrupt", [False, True], ids=["complete", "resume-after-move"])
def test_attachment_heavy_archive_keeps_identity_history_links_and_idempotence(
    config, policy, article_scope, monkeypatch, interrupt
):
    result, note, root, identity, mid = archive_source(config, policy, article_scope)
    original_body = note.read_bytes()
    with database.connect(config.state_root) as db:
        original_history = history(db)
        original_location = publication_locations.get(db, "article", result["publication_id"])
        original_moc = moc_store.load(config, article_scope[1], mid)
        packet = note_placement.preview(
            config, db, *article_scope[:2], identity, digest(original_body)
        )
        assert packet["ready"] and packet["rewritten_notes"] == ["Incoming.md"]
        operation = packet["operation_id"]
        frozen = json.loads(
            db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
        )
        finalize = moc_placement_projection.finalize
        if interrupt:

            def interrupted(config, db, operation, value, root):
                assert value["move_applied"] and value["metadata_committed"]
                raise OSError("interrupted after archive metadata commit")

            monkeypatch.setattr(moc_placement_projection, "finalize", interrupted)
            with pytest.raises(OSError, match="interrupted after archive metadata commit"):
                note_placement_execution.apply(config, db, *article_scope[:2], operation)
            failed = db.execute(
                "SELECT state,payload FROM vault_operations WHERE id=?", (operation,)
            ).fetchone()
            assert failed["state"] == "reconcile"
            assert json.loads(failed["payload"])["metadata_committed"]
            assert (
                not note.exists() and (root / packet["target_path"]).read_bytes() == original_body
            )
            monkeypatch.setattr(moc_placement_projection, "finalize", finalize)
        applied = note_placement_execution.apply(
            config, db, *article_scope[:2], operation, reconcile=interrupt
        )
        assert applied["state"] == "completed" and not note.exists()
        assert (root / applied["target_path"]).read_bytes() == original_body
        assert applied["target_path"] in unquote((root / "Incoming.md").read_text())
        inventory = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        current = next(n for n in inventory.notes if n.identity == identity)
        assert current.relative_path == applied["target_path"]
        assert current.publication_id == result["publication_id"]
        assert moc_store.load(config, article_scope[1], mid) == original_moc
        assert history(db) == original_history
        location = publication_locations.get(db, "article", result["publication_id"])
        assert location["revision"] == original_location["revision"] + 1
        assert location["plan"]["mapping"] == {
            logical: frozen["moves"].get(path, path)
            for logical, path in original_location["plan"]["mapping"].items()
        }
        committed = tuple(
            db.execute("SELECT * FROM vault_operations WHERE id=?", (operation,)).fetchone()
        )
        assert note_placement_execution.apply(config, db, *article_scope[:2], operation) == applied
        assert (
            tuple(db.execute("SELECT * FROM vault_operations WHERE id=?", (operation,)).fetchone())
            == committed
        )
        assert publication_locations.get(db, "article", result["publication_id"]) == location
        assert history(db) == original_history


@pytest.mark.parametrize(
    "failure,expected",
    [("capacity", "layout_metadata_inventory_limit"), ("placeholder", "cloud_item_not_downloaded")],
)
def test_finalization_failure_keeps_exact_reason_and_reconciliation_journal(
    config, policy, article_scope, monkeypatch, failure, expected
):
    result, note, root, identity, mid = archive_source(config, policy, article_scope, attachments=0)
    original_body = note.read_bytes()
    with database.connect(config.state_root) as db:
        original_history = history(db)
        original_location = publication_locations.get(db, "article", result["publication_id"])
        original_moc = moc_store.load(config, article_scope[1], mid)
        packet = note_placement.preview(
            config, db, *article_scope[:2], identity, digest(original_body)
        )
        operation = packet["operation_id"]
        complete = note_placement_execution.complete

        def changed_inventory(config, db, operation, value, root):
            # A full inventory must remain fresh even if files arrive after preview.
            if failure == "capacity":
                files(root / "Attachments/extra", vault_scope.METADATA_ENTRY_LIMIT + 1)
            else:
                (root / "Attachments/extra/pending.icloud").write_bytes(b"pending")
            complete(config, db, operation, value, root)

        monkeypatch.setattr(note_placement_execution, "complete", changed_inventory)
        with pytest.raises(IntegrityError, match="^" + expected + "$"):
            note_placement_execution.apply(config, db, *article_scope[:2], operation)
        row = db.execute(
            "SELECT state,payload FROM vault_operations WHERE id=?", (operation,)
        ).fetchone()
        value = json.loads(row["payload"])
        assert row["state"] == "reconcile" and value["move_applied"]
        assert not value.get("metadata_committed")
        assert not note.exists() and (root / packet["target_path"]).read_bytes() == original_body
        assert (
            publication_locations.get(db, "article", result["publication_id"]) == original_location
        )
        assert moc_store.load(config, article_scope[1], mid) == original_moc
        assert history(db) == original_history
