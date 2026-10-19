from unittest.mock import patch
from urllib.parse import unquote
from uuid import uuid4

import pytest
from test_article_publication import completed, destination
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate

from fiber_bundle import (
    article_publication,
    auth,
    database,
    moc_store,
    note_inventory,
    note_placement,
    note_placement_execution,
)
from fiber_bundle.config import IntegrityError


def published(config, policy, scope, **settings):
    activate(config, scope[1], **settings)
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE client_vault_grants SET can_query=1,can_original=1 WHERE "
            "client_id=? AND vault_id=?",
            scope[:2],
        )
    rid = completed(config, policy, scope)
    result = article_publication.publish(config, scope[0], rid)
    return rid, result, destination(config, result)


def test_archive_updates_images_incoming_moc_and_stable_identity(config, policy, article_scope):
    rid, result, note = published(config, policy, article_scope, image_link_style="markdown")
    root = note.parent.parent
    (root / "Incoming.md").write_text("[incoming](<" + result["note_path"] + ">)\n")
    mid = str(uuid4())
    key = root.stat()
    identity = "publication:" + result["publication_id"]
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record("MOC", key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
        "create",
        0,
    )
    with database.connect(config.state_root) as db:
        packet = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            identity,
            __import__("hashlib").sha256(note.read_bytes()).hexdigest(),
        )
        assert packet["ready"] and packet["target_path"].startswith("Archive/")
        assert packet["rewritten_notes"] == [
            "Incoming.md"
        ]  # Sibling Inbox/Archive have the same relative image prefix.
        applied = note_placement_execution.apply(
            config, db, *article_scope[:2], packet["operation_id"]
        )
        assert applied["state"] == "completed" and not note.exists()
        target = root / applied["target_path"]
        assert target.is_file() and "Archive/" in (root / "Incoming.md").read_text()
        inv = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        current = next(n for n in inv.notes if n.identity == identity)
        assert current.relative_path == applied["target_path"]
        assert moc_store.load(config, article_scope[1], mid).members == {identity: ["manual"]}
        assert (
            article_publication.publish_locked(config, db, article_scope[0], rid)["note_path"]
            == applied["target_path"]
        )
        assert (
            note_placement_execution.apply(config, db, *article_scope[:2], packet["operation_id"])[
                "state"
            ]
            == "completed"
        )


def test_multiple_moc_destinations_require_selection(config, policy, article_scope):
    _, result, note = published(config, policy, article_scope)
    root = note.parent.parent
    key = root.stat()
    identity = "publication:" + result["publication_id"]
    mids = [str(uuid4()), str(uuid4())]
    for mid in mids:
        moc_store.save(
            config,
            article_scope[1],
            mid,
            article_scope[0],
            moc_store.Record(mid, key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
            "create",
            0,
        )
    activate(config, article_scope[1], moc_destinations={mids[0]: "领域/A", mids[1]: "领域/B"})
    with database.connect(config.state_root) as db:
        packet = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            identity,
            __import__("hashlib").sha256(note.read_bytes()).hexdigest(),
        )
        assert not packet["ready"] and packet["destination_choices"] == ["领域/A", "领域/B"]
        with pytest.raises(IntegrityError):
            note_placement_execution.apply(config, db, *article_scope[:2], packet["operation_id"])
        packet = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            identity,
            __import__("hashlib").sha256(note.read_bytes()).hexdigest(),
            destination="领域/B",
        )
        assert (
            note_placement_execution.apply(config, db, *article_scope[:2], packet["operation_id"])[
                "state"
            ]
            == "completed"
        )


def test_unresolved_outbound_blocks_and_external_edit_invalidates_preview(
    config, policy, article_scope
):
    _, result, note = published(config, policy, article_scope)
    identity = "publication:" + result["publication_id"]
    note.write_text(note.read_text() + "\n[[missing]]\n")
    with database.connect(config.state_root) as db:
        packet = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            identity,
            __import__("hashlib").sha256(note.read_bytes()).hexdigest(),
        )
        assert not packet["ready"] and packet["unresolved_links"]
        with pytest.raises(IntegrityError):
            note_placement_execution.apply(config, db, *article_scope[:2], packet["operation_id"])
    assert note.exists()


def test_link_then_failed_archive_retains_association_and_reconciles(config, policy, article_scope):
    _, result, note = published(config, policy, article_scope)
    root = note.parent.parent
    key = root.stat()
    identity = "publication:" + result["publication_id"]
    mid = str(uuid4())
    moc_store.save(
        config,
        article_scope[1],
        mid,
        article_scope[0],
        moc_store.Record("MOC", key.st_dev, key.st_ino, 1, {}),
        "create",
        0,
    )
    with database.connect(config.state_root) as db:
        packet = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            identity,
            __import__("hashlib").sha256(note.read_bytes()).hexdigest(),
            action="link_archive",
            moc_id=mid,
            moc_revision=1,
        )
        with (
            patch.object(note_placement_execution, "move", side_effect=OSError("injected")),
            pytest.raises(OSError),
        ):
            note_placement_execution.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert moc_store.load(config, article_scope[1], mid).members == {identity: ["manual"]}
        with pytest.raises(IntegrityError, match="placement_reconciliation"):
            note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        applied = note_placement_execution.apply(
            config, db, *article_scope[:2], packet["operation_id"], reconcile=True
        )
        assert applied["state"] == "completed" and applied["association_state"] == "associated"
        assert moc_store.load(config, article_scope[1], mid).revision == 2


@pytest.mark.parametrize("boundary", ["exchange", "move"])
def test_crash_after_physical_rename_recovers_known_files(config, policy, article_scope, boundary):
    _, result, note = published(config, policy, article_scope)
    root = note.parent.parent
    incoming = root / "Incoming.md"
    incoming.write_text("[source](<" + result["note_path"] + ">)\n")
    from fiber_bundle import file_exchange
    from fiber_bundle.objects import digest

    with database.connect(config.state_root) as db:
        packet = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            "publication:" + result["publication_id"],
            digest(note.read_bytes()),
        )
        original = file_exchange.rename
        crashed = False

        def fail_after(*args, **kwargs):
            nonlocal crashed
            original(*args, **kwargs)
            if not crashed and kwargs["exchange"] == (boundary == "exchange"):
                crashed = True
                raise OSError("injected after physical rename before journal")

        with patch.object(file_exchange, "rename", side_effect=fail_after), pytest.raises(OSError):
            note_placement_execution.apply(config, db, *article_scope[:2], packet["operation_id"])
        assert crashed
        with pytest.raises(IntegrityError, match="reconciliation"):
            note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        applied = note_placement_execution.apply(
            config, db, *article_scope[:2], packet["operation_id"], reconcile=True
        )
        assert applied["state"] == "completed"
        assert not note.exists() and (root / applied["target_path"]).is_file()
        assert applied["target_path"] in unquote(incoming.read_text())
        inv = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        assert (
            len([n for n in inv.notes if n.identity == "publication:" + result["publication_id"]])
            == 1
        )
