import hashlib
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_execution import policy as policy
from test_weixin_batch_revocation import cancel
from test_weixin_batch_revocation import published as published
from test_weixin_inbox import binding as binding
from test_weixin_publication import note

from fiber_bundle import auth, database, moc_associations, moc_dispatch, moc_store
from fiber_bundle import publication_organization as organization
from fiber_bundle import weixin_batch_revocation as revoke
from fiber_bundle.config import StorageError


@pytest.fixture
def associated(config, binding, published):
    db = database.connect(config.state_root)
    token = "synthetic-batch-moc-token"
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), binding.client_id)
    )
    db.close()
    mid = str(uuid4())
    authorization = "Bearer " + token
    organization.queue(
        config,
        authorization,
        published[1],
        moc_id=mid,
        revision=0,
        relative="Batch MOC.md",
        expected_sha256="absent",
        new_title="Batch MOC",
    )
    moc_dispatch.dispatch(config, authorization, binding.vault_id, enabled=True, maximum=1)
    return mid, authorization


def test_revoke_own_link_and_only_managed_block_leaving_empty_new_moc(
    config, binding, published, associated
):
    mid, _ = associated
    path = note(config, published[2]).parent.parent / "Batch MOC.md"
    path.write_bytes(b"Human prefix\n" + path.read_bytes())
    cancel(config, binding, published)
    assert revoke.run(config, binding, published[0])["results"][0]["state"] == "planned"
    assert revoke.run(config, binding, published[0], "apply")["results"][0]["state"] == "revoked"
    assert moc_store.load(config, binding.vault_id, mid).members == {}
    assert path.exists()  # Never remove a new MOC; it may contain human content.
    result = revoke.run(config, binding, published[0], "project-mocs")
    assert result["moc_projections"][0]["status"] == "applied"
    assert path.read_bytes().startswith(b"Human prefix\n")
    assert "note.md" not in path.read_text()


def test_manual_claim_retains_note_and_association(config, binding, published, associated):
    mid, authorization = associated
    source = note(config, published[2])
    moc_associations.run(
        config,
        authorization,
        binding.vault_id,
        "link",
        mid,
        revision=1,
        note_identity="publication:" + published[2]["publication_id"],
        owner="manual",
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    cancel(config, binding, published)
    result = revoke.run(config, binding, published[0])
    assert result["results"][0]["state"] == "shared_or_associated"
    revoke.run(config, binding, published[0], "apply")
    assert source.exists()
    assert moc_store.load(config, binding.vault_id, mid).members


def test_reconcile_moc_commit_failure_without_replaying_file_move(
    config, binding, published, associated
):
    mid, _ = associated
    cancel(config, binding, published)
    revoke.run(config, binding, published[0])
    original = moc_store.save

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic failure after MOC commit")

    with patch.object(moc_store, "save", side_effect=failed), pytest.raises(OSError):
        revoke.run(config, binding, published[0], "apply")
    assert not note(config, published[2]).exists()
    assert moc_store.load(config, binding.vault_id, mid).members == {}
    from fiber_bundle import file_exchange

    with patch.object(file_exchange, "rename", side_effect=AssertionError("no file replay")):
        result = revoke.run(config, binding, published[0], "reconcile")
    assert result["results"][0]["state"] == "revoked"


def test_changed_managed_block_is_preserved_after_metadata_revoke(
    config, binding, published, associated
):
    path = note(config, published[2]).parent.parent / "Batch MOC.md"
    path.write_bytes(path.read_bytes().replace("系统增量关联".encode(), b"manual changes"))
    before = path.read_bytes()
    cancel(config, binding, published)
    revoke.run(config, binding, published[0])
    revoke.run(config, binding, published[0], "apply")
    result = revoke.run(config, binding, published[0], "project-mocs")
    assert result["moc_projections"][0]["status"] == "conflict_preserved"
    assert path.read_bytes() == before


@pytest.mark.parametrize("human_edit", [False, True])
def test_multiple_new_notes_share_moc_with_successive_cas_and_single_projection(
    config, binding, policy, human_edit
):
    import test_article_capture
    from test_articles import TEXT, current_html
    from test_weixin_publication import articles, complete

    from fiber_bundle import weixin_batches, weixin_publication, weixin_publication_dispatch

    db = database.connect(config.state_root)
    token = "synthetic-multi-batch-token"
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), binding.client_id)
    )
    auth.set_grant(db, binding.client_id, binding.vault_id, query=True, original=False, add=True)
    db.close()
    authorization = "Bearer " + token
    batch, runs = articles(config, binding, policy, count=2)
    for index, rid in enumerate(runs):
        html = current_html().replace(
            json.dumps(TEXT).encode(), json.dumps(TEXT + str(index)).encode()
        )
        with patch.object(test_article_capture, "current_html", return_value=html):
            complete(config, policy, rid)
    weixin_publication.plan(config, binding, batch)
    results = weixin_publication_dispatch.dispatch(config, binding, maximum=2)["results"]
    assert len(results) == 2 and len({r["publication_id"] for r in results}) == 2
    mid = str(uuid4())
    path = note(config, results[0]).parent.parent / "Shared MOC.md"
    for index, rid in enumerate(runs):
        organization.queue(
            config,
            authorization,
            rid,
            moc_id=mid,
            revision=index,
            relative="Shared MOC.md",
            new_title="Shared MOC" if index == 0 else None,
            expected_sha256="absent"
            if index == 0
            else hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        moc_dispatch.dispatch(config, authorization, binding.vault_id, enabled=True, maximum=1)
    assert len(moc_store.load(config, binding.vault_id, mid).members) == 2
    if human_edit:
        note(config, results[0]).write_text("Retained human content")
    weixin_batches.choose(config, binding, batch, "cancel")
    plan = revoke.run(config, binding, batch)
    assert [r["state"] for r in plan["results"]] == ["planned", "planned"]
    applied = revoke.run(config, binding, batch, "apply")
    assert {r["publication_id"]: r["state"] for r in applied["results"]} == {
        r["publication_id"]: "links_revoked" if human_edit and r == results[0] else "revoked"
        for r in results
    }
    assert moc_store.load(config, binding.vault_id, mid).members == {}
    assert all(note(config, r).exists() == (human_edit and r == results[0]) for r in results)
    projected = revoke.run(config, binding, batch, "project-mocs")
    assert len(projected["moc_projections"]) == 1
    assert projected["moc_projections"][0]["status"] == "applied"
    assert "note.md" not in path.read_text()


def test_human_edit_after_moc_commit_retains_file_and_unresolved_claim(
    config, binding, published, associated
):
    source = note(config, published[2])
    cancel(config, binding, published)
    revoke.run(config, binding, published[0])
    original = moc_store.save

    def edited(*args, **kwargs):
        original(*args, **kwargs)
        next(source.parent.glob(".fiber-revoked-*.md")).write_text("Human edited retained note")

    with patch.object(moc_store, "save", side_effect=edited), pytest.raises(StorageError):
        revoke.run(config, binding, published[0], "apply")
    assert revoke.run(config, binding, published[0], "status")["results"][0]["state"] == "claimed"
    with pytest.raises(StorageError):
        revoke.run(config, binding, published[0], "reconcile")
    assert (
        next(source.parent.glob(".fiber-revoked-*.md")).read_text() == "Human edited retained note"
    )


def test_rechecks_permission_before_each_moc_commit(config, binding, published, associated):
    first, authorization = associated
    second = str(uuid4())
    organization.queue(
        config,
        authorization,
        published[1],
        moc_id=second,
        revision=0,
        relative="Second.md",
        expected_sha256="absent",
        new_title="Second",
    )
    cancel(config, binding, published)
    revoke.run(config, binding, published[0])
    original = moc_store.save

    def denied(*args, **kwargs):
        original(*args, **kwargs)
        db = database.connect(config.state_root)
        auth.set_grant(
            db, binding.client_id, binding.vault_id, query=True, original=False, add=False
        )
        db.close()

    with patch.object(moc_store, "save", side_effect=denied), pytest.raises(auth.Denied):
        revoke.run(config, binding, published[0], "apply")
    assert (
        sum(bool(moc_store.load(config, binding.vault_id, mid).members) for mid in (first, second))
        == 1
    )
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=True, original=False, add=True)
    db.close()
    result = revoke.run(config, binding, published[0], "reconcile")
    assert result["results"][0]["state"] == "revoked"
    assert all(
        moc_store.load(config, binding.vault_id, mid).members == {} for mid in (first, second)
    )


@pytest.mark.parametrize("when", ["before_plan", "after_plan"])
def test_edited_note_retained_while_own_new_links_revoked(
    config, binding, published, associated, when
):
    mid, _ = associated
    source = note(config, published[2])
    cancel(config, binding, published)
    if when == "after_plan":
        revoke.run(config, binding, published[0])
    source.write_text("Human retained content")
    if when == "before_plan":
        assert revoke.run(config, binding, published[0])["results"][0]["state"] == "planned"
    result = revoke.run(config, binding, published[0], "apply")["results"][0]
    assert result["state"] == "links_revoked" and result["note_retained"]
    assert source.read_text() == "Human retained content"
    assert not list(source.parent.glob(".fiber-revoked-*.md"))
    assert moc_store.load(config, binding.vault_id, mid).members == {}
    assert (
        revoke.run(config, binding, published[0], "project-mocs")["moc_projections"][0]["status"]
        == "applied"
    )


def test_other_manual_moc_retained_independently(config, binding, published, associated):
    own, authorization = associated
    manual = str(uuid4())
    source = note(config, published[2])
    moc_associations.run(
        config, authorization, binding.vault_id, "create", manual, title="Manual", revision=0
    )
    moc_associations.run(
        config,
        authorization,
        binding.vault_id,
        "link",
        manual,
        revision=1,
        note_identity="publication:" + published[2]["publication_id"],
        owner="manual",
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    manual_record = moc_store.load(config, binding.vault_id, manual)
    cancel(config, binding, published)
    revoke.run(config, binding, published[0])
    result = revoke.run(config, binding, published[0], "apply")["results"][0]
    assert result["state"] == "links_revoked" and source.exists()
    assert moc_store.load(config, binding.vault_id, own).members == {}
    assert moc_store.load(config, binding.vault_id, manual) == manual_record


def test_existing_note_reused_by_batch_retained_own_added_moc_withdrawn(
    config, binding, policy, published, associated
):
    from test_weixin_publication import articles, complete

    from fiber_bundle import weixin_batches, weixin_publication, weixin_publication_dispatch

    old, authorization = associated
    batch, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    weixin_publication.plan(config, binding, batch)
    result = weixin_publication_dispatch.dispatch(config, binding, maximum=1)["results"][0]
    assert result["publication_id"] == published[2]["publication_id"]
    added = str(uuid4())
    organization.queue(
        config,
        authorization,
        runs[0],
        moc_id=added,
        revision=0,
        relative="Added.md",
        expected_sha256="absent",
        new_title="Added",
    )
    old_record = moc_store.load(config, binding.vault_id, old)
    weixin_batches.choose(config, binding, batch, "cancel")
    revoke.run(config, binding, batch)
    outcome = revoke.run(config, binding, batch, "apply")["results"][0]
    assert outcome["state"] == "links_revoked" and outcome["note_reason"] == "existing_or_unproven"
    assert note(config, result).exists()
    assert moc_store.load(config, binding.vault_id, added).members == {}
    assert moc_store.load(config, binding.vault_id, old) == old_record


@pytest.mark.parametrize("when", ["before_plan", "after_plan"])
def test_second_task_explicitly_claiming_same_link_protects_it(
    config, binding, policy, published, associated, when
):
    from test_weixin_publication import articles, complete

    from fiber_bundle import weixin_publication, weixin_publication_dispatch

    mid, authorization = associated
    batch, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    weixin_publication.plan(config, binding, batch)
    result = weixin_publication_dispatch.dispatch(config, binding, maximum=1)["results"][0]
    cancel(config, binding, published)
    if when == "after_plan":
        revoke.run(config, binding, published[0])
    path = note(config, result).parent.parent / "Batch MOC.md"
    organization.queue(
        config,
        authorization,
        runs[0],
        moc_id=mid,
        revision=1,
        relative="Batch MOC.md",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    record = moc_store.load(config, binding.vault_id, mid)
    if when == "before_plan":
        assert (
            revoke.run(config, binding, published[0])["results"][0]["state"]
            == "shared_or_associated"
        )
        revoke.run(config, binding, published[0], "apply")
    else:
        with pytest.raises(StorageError):
            revoke.run(config, binding, published[0], "apply")
        assert (
            revoke.run(config, binding, published[0], "status")["results"][0]["state"] == "planned"
        )
    assert note(config, result).exists()
    assert moc_store.load(config, binding.vault_id, mid) == record


def test_links_only_commit_failure_reconciles_without_file_replay(
    config, binding, published, associated
):
    from fiber_bundle import file_exchange

    mid, _ = associated
    source = note(config, published[2])
    source.write_text("Retained edit")
    cancel(config, binding, published)
    revoke.run(config, binding, published[0])
    original = moc_store.save

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic failure after link commit")

    with patch.object(moc_store, "save", side_effect=failed), pytest.raises(OSError):
        revoke.run(config, binding, published[0], "apply")
    assert (
        revoke.run(config, binding, published[0], "status")["results"][0]["state"]
        == "links_claimed"
    )
    with pytest.raises(StorageError):
        revoke.run(config, binding, published[0], "project-mocs")
    with patch.object(file_exchange, "rename", side_effect=AssertionError("no file replay")):
        result = revoke.run(config, binding, published[0], "reconcile")
    assert result["results"][0]["state"] == "links_revoked"
    assert source.read_text() == "Retained edit"
    assert moc_store.load(config, binding.vault_id, mid).members == {}
