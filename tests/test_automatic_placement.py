"""Committed association queue, fault recovery and default Resource placement."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published

from fiber_bundle import auth, automatic_placement, database, moc_store, note_placement
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


def setup(config, policy, article_scope, **settings):
    value = settings_wire(unified_settings())
    value.pop("layout_mode")
    value.update(moc_archive_mode="automatic", **settings)
    _, result, note = published(config, policy, article_scope, **value)
    return result, note


def associate(config, scope, result, note, mids=None):
    root = note.parent.parent
    key = root.stat()
    identity = "publication:" + result["publication_id"]
    for mid in mids or [str(uuid4())]:
        moc_store.save(
            config,
            scope[1],
            mid,
            scope[0],
            moc_store.Record(mid, key.st_dev, key.st_ino, 1, {identity: ["manual"]}),
            "create",
            0,
        )
    with database.connect(config.state_root) as db:
        queued = note_placement.prepare_after_moc(config, db, *scope[:2], [identity])
    return identity, queued


def rows(config, scope):
    with database.connect(config.state_root) as db:
        return automatic_placement.listing(db, *scope[:2])


def test_committed_multi_moc_default_moves_once_and_reindexes(config, policy, article_scope):
    result, note = setup(config, policy, article_scope)
    old = note.read_bytes()
    identity, queued = associate(config, article_scope, result, note, [str(uuid4()), str(uuid4())])
    _, duplicate = associate_again(config, article_scope, identity)
    assert queued == duplicate and note.exists()
    assert automatic_placement.tick(config)
    assert not automatic_placement.tick(config)
    assert not note.exists()
    target = note.parent.parent / "Resource" / note.name
    assert target.read_bytes() == old
    assert rows(config, article_scope)[0]["state"] == "complete"
    with database.connect(config.state_root) as db:
        inventory = note_placement.listing(config, db, *article_scope[:2])
        assert any(
            n["note_identity"] == identity and n["note_path"].startswith("Resource/")
            for n in inventory["notes"]
        )


def associate_again(config, scope, identity):
    with database.connect(config.state_root) as db:
        return identity, note_placement.prepare_after_moc(config, db, *scope[:2], [identity])


@pytest.mark.parametrize("fault", ["edit", "grant", "policy", "cancel"])
def test_queue_rechecks_frozen_authority_and_bytes(config, policy, article_scope, fault):
    result, note = setup(config, policy, article_scope)
    identity, queued = associate(config, article_scope, result, note)
    with database.connect(config.state_root) as db:
        if fault == "edit":
            note.write_text(note.read_text() + "\nhuman edit\n")
        elif fault == "grant":
            auth.set_grant(db, *article_scope[:2], query=True, original=False, add=False)
            auth.set_grant(db, *article_scope[:2], query=True, original=True, add=True)
        elif fault == "policy":
            db.execute("UPDATE vault_save_policies SET revision=revision+1")
        else:
            automatic_placement.reconcile(
                config, db, *article_scope[:2], queued[0]["operation_id"], "cancel"
            )
    automatic_placement.tick(config)
    assert note.exists() and rows(config, article_scope)[0]["state"] in {
        "needs_review",
        "cancelled",
    }
    assert all(
        identity in r.members for r in note_placement.records(config, article_scope[1]).values()
    )


def test_conflicting_moc_directories_wait_for_choice(config, policy, article_scope):
    mids = [str(uuid4()), str(uuid4())]
    # MOCs must exist before activating the directory override.
    result, note = setup(config, policy, article_scope)
    identity, _ = associate(config, article_scope, result, note, mids)
    from test_publication_layout import activate

    value = settings_wire(unified_settings())
    value.pop("layout_mode")
    activate(
        config,
        article_scope[1],
        **{
            **value,
            "moc_archive_mode": "automatic",
            "moc_destinations": {mids[0]: "领域/A", mids[1]: "领域/B"},
        },
    )
    associate_again(config, article_scope, identity)
    assert automatic_placement.tick(config)
    assert note.exists() and rows(config, article_scope)[0]["state"] == "needs_selection"


def test_failed_move_retains_moc_and_explicit_reconciliation(config, policy, article_scope):
    result, note = setup(config, policy, article_scope)
    identity, queued = associate(config, article_scope, result, note)
    with patch(
        "fiber_bundle.note_placement_execution.move", side_effect=OSError("synthetic fault")
    ):
        assert automatic_placement.tick(config)
    assert note.exists() and rows(config, article_scope)[0]["state"] == "reconcile"
    automatic_placement.recover(config)
    assert not automatic_placement.tick(config)
    with database.connect(config.state_root) as db:
        automatic_placement.reconcile(
            config, db, *article_scope[:2], queued[0]["operation_id"], "reconcile"
        )
    assert automatic_placement.tick(config)
    assert rows(config, article_scope)[0]["state"] == "complete" and not note.exists()
    assert all(
        identity in r.members for r in note_placement.records(config, article_scope[1]).values()
    )


def test_preview_only_keeps_legacy_behavior(config, policy, article_scope):
    _, result, note = published(config, policy, article_scope, archive_on_moc=True)
    _, queued = associate(config, article_scope, result, note)
    assert queued[0]["state"] == "prepared"
    assert not automatic_placement.tick(config) and note.exists()
