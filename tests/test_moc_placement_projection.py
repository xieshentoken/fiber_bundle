"""Classification changes both committed membership and the existing MOC file, with recovery."""

import hashlib
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published

from fiber_bundle import (
    auth,
    database,
    moc_file_journal,
    moc_files,
    moc_store,
    note_identities,
    note_placement,
    note_placement_execution,
)
from fiber_bundle.config import IntegrityError


def setup(config, policy, scope, tracked, member):
    _, result, note = published(config, policy, scope)
    root = note.parent.parent
    key = root.stat()
    mid = str(uuid4())
    identity = "publication:" + result["publication_id"]
    record = moc_store.Record(
        "分类目录", key.st_dev, key.st_ino, 1, {identity: ["manual"]} if member else {}
    )
    moc_store.save(config, scope[1], mid, scope[0], record, "create", 0)
    target = root / "MOC.md"
    target.write_text("# 人工目录\n\n保留这段人工正文。\n")
    if tracked:
        with database.connect(config.state_root) as db:
            c = auth.create_client(db, "MOC registration", False)
            auth.set_grant(db, c["id"], scope[1], query=True, original=True, add=True)
        note_identities.run(
            config,
            "Bearer " + c["token"],
            scope[1],
            "track",
            target.name,
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )
    with database.connect(config.state_root) as db:
        p = moc_files.plan_file(
            config,
            db,
            *scope[:2],
            mid,
            1,
            target.name,
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )
        moc_files.apply_file(config, db, *scope[:2], mid, p["plan_id"])
    return mid, result, note, target, identity


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize("action", ["associate_only", "link_archive"])
def test_manual_membership_updates_moc_file_and_keeps_next_projection_usable(
    config, policy, article_scope, tracked, action
):
    mid, result, note, moc, identity = setup(config, policy, article_scope, tracked, False)
    with database.connect(config.state_root) as db:
        p = note_placement.preview(
            config,
            db,
            *article_scope[:2],
            identity,
            hashlib.sha256(note.read_bytes()).hexdigest(),
            action=action,
            moc_id=mid,
            moc_revision=1,
        )
        assert moc.name in p["rewritten_notes"]
        r = note_placement_execution.apply(config, db, *article_scope[:2], p["operation_id"])
        assert r["state"] == "completed"
        assert (
            "保留这段人工正文。" in moc.read_text()
            and note.name.replace(" ", "%20") in moc.read_text()
        )
        assert ("Archive/" in moc.read_text()) == (action == "link_archive")
        assert moc_store.load(config, article_scope[1], mid).members[identity] == ["manual"]
        fresh = moc_files.plan_file(
            config,
            db,
            *article_scope[:2],
            mid,
            2,
            moc.name,
            hashlib.sha256(moc.read_bytes()).hexdigest(),
        )
        assert fresh["status"] == "unchanged"


@pytest.mark.parametrize("failure", ["record_applied", "finish"])
def test_existing_membership_archive_updates_journal_and_recovers_after_journal_failure(
    config, policy, article_scope, failure
):
    mid, result, note, moc, identity = setup(config, policy, article_scope, True, True)
    with database.connect(config.state_root) as db:
        packet = note_placement.preview(
            config, db, *article_scope[:2], identity, hashlib.sha256(note.read_bytes()).hexdigest()
        )
        with patch.object(moc_file_journal, failure, side_effect=OSError("synthetic interruption")):
            with pytest.raises(OSError):
                note_placement_execution.apply(
                    config, db, *article_scope[:2], packet["operation_id"]
                )
        assert (
            db.execute(
                "select state from vault_operations where id=?", (packet["operation_id"],)
            ).fetchone()[0]
            == "reconcile"
        )
        result = note_placement_execution.apply(
            config, db, *article_scope[:2], packet["operation_id"], reconcile=True
        )
        assert (
            result["state"] == "completed" and not note.exists() and "Archive/" in moc.read_text()
        )
        fresh = moc_files.plan_file(
            config,
            db,
            *article_scope[:2],
            mid,
            1,
            moc.name,
            hashlib.sha256(moc.read_bytes()).hexdigest(),
        )
        assert fresh["status"] == "unchanged"


def test_edited_managed_block_is_not_overwritten_by_archive(config, policy, article_scope):
    mid, result, note, moc, identity = setup(config, policy, article_scope, False, True)
    moc.write_text(moc.read_text().replace("系统增量关联", "人工改动的受管块"))
    before = moc.read_bytes()
    with (
        database.connect(config.state_root) as db,
        pytest.raises(IntegrityError, match="human edited"),
    ):
        note_placement.preview(
            config, db, *article_scope[:2], identity, hashlib.sha256(note.read_bytes()).hexdigest()
        )
    assert moc.read_bytes() == before and note.exists()
