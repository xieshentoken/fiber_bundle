import os
import sqlite3
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_moc_dispatch import audit, dispatch, moc, reconcile, schedule, state
from test_moc_dispatch import dispatch_scope as dispatch_scope
from test_note_moves import published as published
from test_weixin_inbox import binding as binding
from test_weixin_publication import articles, complete

from fiber_bundle import (
    auth,
    database,
    file_exchange,
    moc_dispatch_store,
    moc_file_journal,
    moc_files,
    note_identity_store,
    weixin_batches,
    weixin_publication,
)
from fiber_bundle.config import StorageError


@pytest.mark.parametrize("stage", ["schedule", "dispatch", "exchange"])
def test_real_weixin_batch_cancel_gate_is_inherited(config, binding, policy, stage):
    bid, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    weixin_publication.plan(config, binding, bid)
    result = weixin_publication.reconcile(config, binding, runs[0])
    db = database.connect(config.state_root)
    token = "synthetic-bound-cancel-token"
    db.execute(
        "UPDATE clients SET token_digest=? WHERE id=?", (auth.digest(token), binding.client_id)
    )
    auth.set_grant(db, binding.client_id, binding.vault_id, query=True, add=True, original=False)
    root = config.project_root / "test vault/temp_vault1"
    scope = {
        "authorization": "Bearer " + token,
        "cid": binding.client_id,
        "vid": binding.vault_id,
        "rid": runs[0],
        "pid": result["publication_id"],
        "root": root,
        "note": root / result["note_path"],
        "db": db,
    }
    try:
        target = moc(config, scope, track=True)
        path = root / target[1]
        original, inode = path.read_bytes(), path.stat().st_ino
        if stage == "schedule":
            weixin_batches.choose(config, binding, bid, "cancel")
            with pytest.raises(auth.Denied):
                schedule(config, scope, target)
            assert not (config.state_root / "moc-dispatch.sqlite3").exists()
        else:
            job = schedule(config, scope, target)
            if stage == "dispatch":
                weixin_batches.choose(config, binding, bid, "cancel")
                assert dispatch(config, scope)["state"] == "stopped"
            else:
                rename = file_exchange.rename
                calls = 0

                def cancel(*args, **kwargs):
                    nonlocal calls
                    result = rename(*args, **kwargs)
                    calls += 1
                    if calls == 1:
                        journal = sqlite3.connect(config.state_root / "weixin-inbox.sqlite3")
                        journal.execute(
                            "UPDATE wx_batches SET state='cancelled',action='cancel' WHERE id=?",
                            (bid,),
                        )
                        journal.commit()
                        journal.close()
                    return result

                with patch.object(file_exchange, "rename", side_effect=cancel):
                    assert dispatch(config, scope)["state"] == "stopped"
            assert reconcile(config, scope, job)["state"] == "not-applied"
        assert (path.read_bytes(), path.stat().st_ino) == (original, inode)
        assert note_identity_store.records(config, binding.vault_id)[0].revision == 1
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["queue-event", "completion-event", "after-completion"])
def test_queue_audit_failures_do_not_replay_file_effects(config, dispatch_scope, failure):
    scope = dispatch_scope
    target = moc(config, scope, track=True)
    path = scope["root"] / target[1]
    original = path.read_bytes()
    if failure == "queue-event":
        with moc_dispatch_store.connection(config, write=True) as db:
            db.execute(
                "CREATE TRIGGER fail_event BEFORE INSERT ON events "
                "BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END"
            )
        with pytest.raises(sqlite3.Error):
            schedule(config, scope, target)
        assert moc_dispatch_store.jobs(config, scope["cid"], scope["vid"]) == []
        assert path.read_bytes() == original
        assert moc_file_journal.plans(config, scope["vid"], target[0])[0].status == "planned"
        return
    job = schedule(config, scope, target)
    if failure == "completion-event":
        with moc_dispatch_store.connection(config, write=True) as db:
            db.execute(
                "CREATE TRIGGER fail_event BEFORE INSERT ON events "
                "WHEN NEW.state='applied' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
            )
        assert dispatch(config, scope)["state"] == "stopped"
        assert state(config, scope, job)["state"] == "failed"
        with moc_dispatch_store.connection(config, write=True) as db:
            db.execute("DROP TRIGGER fail_event")
        before = path.read_bytes(), path.stat().st_ino
        assert reconcile(config, scope, job)["state"] == "applied"
        assert (path.read_bytes(), path.stat().st_ino) == before
    else:
        transition = moc_dispatch_store.transition

        def after(*args, **kwargs):
            result = transition(*args, **kwargs)
            if args[2] == "applied":
                raise OSError("synthetic after-commit failure")
            return result

        with patch.object(moc_dispatch_store, "transition", side_effect=after):
            assert dispatch(config, scope)["state"] == "stopped"
        assert state(config, scope, job)["state"] == "applied"
    assert path.read_bytes() != original
    assert dispatch(config, scope)["processed"] == 0
    assert note_identity_store.records(config, scope["vid"])[0].revision == 2


def test_unchanged_job_and_previously_manual_applied_job_are_not_rewritten(config, dispatch_scope):
    scope = dispatch_scope
    target = moc(config, scope)
    job = schedule(config, scope, target)
    moc_files.run(
        config,
        scope["authorization"],
        scope["vid"],
        "apply-file",
        target[0],
        plan_id=job["plan_id"],
    )
    inode = (scope["root"] / target[1]).stat().st_ino
    assert dispatch(config, scope)["results"][0]["moc_file_write_performed"] is False
    assert (scope["root"] / target[1]).stat().st_ino == inode
    unchanged = schedule(config, scope, target)
    assert unchanged["state"] == "unchanged" and unchanged["plan_id"] is None
    assert audit(config, unchanged) == ["unchanged"]
    assert dispatch(config, scope)["processed"] == 0


@pytest.mark.parametrize("change", ["unrelated-edit", "manual-resolution"])
def test_source_fingerprint_tracks_actual_dependencies(config, dispatch_scope, change):
    scope = dispatch_scope
    target = moc(config, scope)
    path = scope["root"] / target[1]
    job = schedule(config, scope, target)
    if change == "unrelated-edit":
        (scope["root"] / "unrelated.md").write_text("Not a member or manual link")
        assert dispatch(config, scope)["state"] == "limit_reached"
        assert state(config, scope, job)["state"] == "applied"
    else:
        (scope["root"] / "manual.md").write_text("Now resolves previously unknown human wikilink")
        before = path.read_bytes()
        assert dispatch(config, scope)["state"] == "stopped"
        assert path.read_bytes() == before


def test_queue_capacity_checked_before_creating_more_plans(config, dispatch_scope):
    scope = dispatch_scope
    target = moc(config, scope)
    schedule(config, scope, target)
    with patch.object(moc_dispatch_store, "MAX_JOBS", 1), pytest.raises(StorageError):
        schedule(config, scope, target, request_id=str(uuid4()))
    assert len(moc_file_journal.plans(config, scope["vid"], target[0])) == 1


def test_new_explicit_request_after_reconcile_can_retry_without_reusing_failed_intent(
    config, dispatch_scope
):
    scope = dispatch_scope
    target = moc(config, scope)
    job = schedule(config, scope, target)
    with patch.object(moc_files, "apply_file", side_effect=OSError("pre-write failure")):
        assert dispatch(config, scope)["state"] == "stopped"
    assert reconcile(config, scope, job)["state"] == "not-applied"
    assert schedule(config, scope, target)["job_id"] == job["job_id"]
    fresh = schedule(config, scope, target, request_id=str(uuid4()))
    assert fresh["job_id"] != job["job_id"]
    assert dispatch(config, scope)["results"][0]["job_id"] == fresh["job_id"]


@pytest.mark.parametrize("danger", ["symlink", "hardlink", "schema", "metadata", "plan-pointer"])
def test_unsafe_or_tampered_dispatch_store_fails_closed(config, dispatch_scope, danger):
    scope = dispatch_scope
    target = moc(config, scope)
    schedule(config, scope, target)
    path = config.state_root / "moc-dispatch.sqlite3"
    if danger in {"symlink", "hardlink"}:
        original = config.state_root / "original-queue.sqlite3"
        path.rename(original)
        if danger == "symlink":
            path.symlink_to(original)
        else:
            os.link(original, path)
    else:
        db = sqlite3.connect(path)
        if danger == "schema":
            db.execute("PRAGMA user_version=99")
        elif danger == "metadata":
            db.execute("UPDATE jobs SET request_json='{}'")
        else:
            db.execute("UPDATE jobs SET plan_id=?", ("0" * 64,))
        db.commit()
        db.close()
    before = (scope["root"] / target[1]).read_bytes()
    if danger == "plan-pointer":
        assert dispatch(config, scope)["state"] == "stopped"
    else:
        with pytest.raises((StorageError, OSError)):
            dispatch(config, scope)
    assert (scope["root"] / target[1]).read_bytes() == before


@pytest.mark.parametrize("field", ["run_id", "publication_id", "client_id", "path", "revision"])
def test_source_bound_plan_contract_is_strict(config, dispatch_scope, field):
    scope = dispatch_scope
    target = moc(config, scope)
    schedule(config, scope, target)
    value = moc_file_journal.plans(config, scope["vid"], target[0])[0].value
    value["dispatch_source"][field] = None
    with pytest.raises(StorageError):
        moc_file_journal.validate(value, scope["vid"], target[0])


def test_other_publication_owner_cannot_schedule_even_with_same_vault_grants(
    config, dispatch_scope
):
    scope = dispatch_scope
    target = moc(config, scope)
    other = auth.create_client(scope["db"], "other-owner", False)
    auth.set_grant(scope["db"], other["id"], scope["vid"], query=True, add=True, original=False)
    changed = {**scope, "authorization": "Bearer " + other["token"]}
    with pytest.raises(auth.Denied):
        schedule(config, changed, target)
    assert not (config.state_root / "moc-dispatch.sqlite3").exists()
    assert moc_file_journal.plans(config, scope["vid"], target[0]) == []


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_switch_must_be_actual_boolean(config, value):
    from fiber_bundle import moc_dispatch

    with pytest.raises(StorageError):
        moc_dispatch.dispatch(config, "invalid", "invalid", enabled=value, maximum=1)


@pytest.mark.parametrize("change", ["cancel", "source-missing", "membership-removed", "permission"])
def test_recovery_confirms_existing_effect_without_replaying_cancelled_work(
    config, dispatch_scope, change
):
    scope = dispatch_scope
    target = moc(config, scope, track=True)
    job = schedule(config, scope, target)
    path = scope["root"] / target[1]
    rename = file_exchange.rename

    def die(*args, **kwargs):
        rename(*args, **kwargs)
        raise SystemExit

    with patch.object(file_exchange, "rename", side_effect=die), pytest.raises(SystemExit):
        dispatch(config, scope)
    assert state(config, scope, job)["state"] == "claimed"
    if change == "cancel":
        scope["db"].execute(
            "UPDATE execution_runs SET state='cancelled' WHERE id=?", (scope["rid"],)
        )
    elif change == "source-missing":
        scope["note"].unlink()
    elif change == "membership-removed":
        # Simulate a competing metadata change without attempting to query the
        # intentionally unresolved target identity before its explicit recovery.
        from dataclasses import replace

        from fiber_bundle import moc_store

        old = moc_store.load(config, scope["vid"], target[0])
        moc_store.save(
            config,
            scope["vid"],
            target[0],
            scope["cid"],
            replace(old, revision=3, members={}),
            "unlink-system",
            2,
        )
    else:
        auth.set_grant(
            scope["db"], scope["cid"], scope["vid"], query=True, add=False, original=False
        )
    before = path.read_bytes(), path.stat().st_ino
    if change == "permission":
        with pytest.raises(auth.Denied):
            reconcile(config, scope, job)
        assert note_identity_store.records(config, scope["vid"])[0].revision == 1
    else:
        assert reconcile(config, scope, job)["state"] == "applied"
        assert note_identity_store.records(config, scope["vid"])[0].revision == 2
        assert dispatch(config, scope)["processed"] == 0
    assert (path.read_bytes(), path.stat().st_ino) == before
