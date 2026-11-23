"""Admin-only committed archive verification never resumes the owning task."""

import copy
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_moc_recovery import historical_map
from test_note_moc_locations import managed

from fiber_bundle import (
    auth,
    database,
    moc_placement_projection,
    moc_store,
    note_placement_execution,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import execution_control, maintenance, store
from fiber_bundle.organization import maintenance_committed_verification as verification


def interrupted(config, ready, *, second=True, managed_note=False):
    cid, vid, root, *_ = ready
    if second:
        (root / "Inbox/待处理.md").write_text("# 待处理\n#热管理\n人工剩余正文\n")
    if managed_note:
        managed(config, ready)
    with database.connect(config.state_root) as db:
        historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(
            moc_placement_projection, "finalize", side_effect=OSError("synthetic interruption")
        ):
            failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial"
    return failed


def no_replay(stack):
    for name in ("exchange", "move", "complete"):
        stack.enter_context(
            patch.object(
                note_placement_execution,
                name,
                side_effect=AssertionError("replayed write: " + name),
            )
        )
    for name in (
        "fiber_bundle.note_search_build.build",
        "fiber_bundle.vault_sync.index.update",
        "fiber_bundle.vault_sync.commands.submit",
        "fiber_bundle.organization.maintenance.run",
    ):
        stack.enter_context(patch(name, side_effect=AssertionError("unexpected work: " + name)))


def evidence(root):
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_ino)
        for p in root.rglob("*.md")
    }


def test_verifies_committed_only_preserves_pending_errors_manual_relations_and_history(
    config, ready
):
    cid, vid, root, *_ = ready
    job = interrupted(config, ready)
    pending = copy.deepcopy([item for item in job["items"] if item["state"] == "pending"])
    assert len(pending) == 1
    before = evidence(root)
    members = {mid: moc_store.load(config, vid, mid) for mid in job["moc_expected"]}
    original_error = job["error"]
    failed_item_error = next(i["error"] for i in job["items"] if i.get("archive_operation"))
    with database.locked(config), database.connect(config.state_root) as db, ExitStack() as stack:
        token = verification.projection(config, db, job)["plan_sha256"]
        no_replay(stack)
        result = maintenance.action(config, db, job, "verify_committed_archives", token)
        assert result["archive_verifications"][-1]["state"] == "verified", result[
            "archive_verifications"
        ]
        assert result["state"] == "partial" and result["error"] == original_error
        assert [i for i in result["items"] if i["state"] == "pending"] == pending
        completed = next(i for i in result["items"] if i.get("archive_operation"))
        assert completed["state"] == "done" and completed["error"] == failed_item_error
        assert (
            db.execute(
                "SELECT state FROM vault_operations WHERE id=?", (completed["archive_operation"],)
            ).fetchone()[0]
            == "completed"
        )
        assert store.get(config, "maintenance_undo", job["id"])["state"] == "ready"
        assert maintenance.action(config, db, result, "verify_committed_archives", token) == result
    assert evidence(root) == before
    assert {mid: moc_store.load(config, vid, mid) for mid in members} == members


@pytest.mark.parametrize("change", ["uncommitted", "owner", "revoked", "cas", "active"])
def test_rejects_unsubmitted_foreign_revoked_stale_or_active(config, ready, change):
    job = interrupted(config, ready, second=False)
    operation = job["items"][0]["archive_operation"]
    with database.locked(config), database.connect(config.state_root) as db:
        token = verification.projection(config, db, job)["plan_sha256"]
        if change in {"uncommitted", "owner"}:
            payload = json.loads(
                db.execute(
                    "SELECT payload FROM vault_operations WHERE id=?", (operation,)
                ).fetchone()[0]
            )
            if change == "uncommitted":
                payload["metadata_committed"] = False
            else:
                payload["client_id"] = "different-owner"
            db.execute(
                "UPDATE vault_operations SET payload=? WHERE id=?", (json.dumps(payload), operation)
            )
        elif change == "revoked":
            auth.set_grant(
                db, job["client_id"], job["vault_id"], query=True, add=False, original=False
            )
        elif change == "cas":
            newer = copy.deepcopy(job)
            newer["generation"] = newer.get("generation", 0) + 1
            store.put(config, "jobs", job["id"], job["vault_id"], newer)
        if change == "active":
            with execution_control.claim(config, job["id"]) as claimed:
                assert claimed
                with pytest.raises(InvalidInput, match="pause_required"):
                    verification.apply(config, db, job, token)
        else:
            with pytest.raises((IntegrityError, InvalidInput, auth.Denied)):
                verification.apply(config, db, job, token)
        assert (
            db.execute("SELECT state FROM vault_operations WHERE id=?", (operation,)).fetchone()[0]
            == "reconcile"
        )


def test_conflict_receipt_retains_original_error_and_is_idempotent(config, ready):
    job = interrupted(config, ready, second=False)
    with database.locked(config), database.connect(config.state_root) as db:
        token = verification.projection(config, db, job)["plan_sha256"]
        with patch.object(
            moc_placement_projection, "finalize", side_effect=OSError("synthetic uncertain outcome")
        ) as finalizer:
            result = verification.apply(config, db, job, token)
            assert result["archive_verifications"][-1]["state"] == "unknown"
            assert result["error"] == job["error"] and result["state"] == "partial"
            assert verification.apply(config, db, result, token) == result
            assert finalizer.call_count == 1
        assert (
            verification.projection(config, db, result)["conflict"]
            == "maintenance_verification_outcome_unknown"
        )


def test_managed_committed_verification_uses_original_finalizer_without_index(config, ready):
    job = interrupted(config, ready, second=False, managed_note=True)
    assert job["items"][0]["archive_kind"] == "managed"
    with database.locked(config), database.connect(config.state_root) as db, ExitStack() as stack:
        token = verification.projection(config, db, job)["plan_sha256"]
        no_replay(stack)
        result = verification.apply(config, db, job, token)
        assert result["archive_verifications"][-1]["state"] == "verified", result[
            "archive_verifications"
        ]
        assert result["state"] == "partial" and result["items"][0]["state"] == "done"


def test_real_namespace_conflict_receipt_preserves_original_diagnostic(config, ready):
    job = interrupted(config, ready)
    root = ready[2]
    original = copy.deepcopy(job)
    pending = copy.deepcopy([item for item in job["items"] if item["state"] == "pending"])
    (root / "Resource/new-attachment.txt").write_text("synthetic unrelated new file")
    before = evidence(root)
    with database.locked(config), database.connect(config.state_root) as db:
        token = verification.projection(config, db, job)["plan_sha256"]
        result = verification.apply(config, db, job, token)
        receipt = result["archive_verifications"][-1]
        assert receipt["state"] == "conflict"
        assert receipt["error"] == "placement_link_sources_changed"
        assert receipt["detail"]["reason"] == "link_paths_changed"
        assert receipt["detail"]["phase"] == "archive_link_verify"
        assert receipt["detail"]["changed_fields"] == ["path_sha256"]
        assert receipt["detail"]["retryable_read"] is False
        assert result["state"] == "partial" and result["error"] == original["error"]
        assert result.get("error_detail") == original.get("error_detail")
        assert [item for item in result["items"] if item["state"] == "pending"] == pending
        assert verification.apply(config, db, result, token) == result
    assert evidence(root) == before


def test_verification_releases_legacy_index_cooldown_without_prior_scheduler(config, ready):
    import time

    from test_archive_observer_publication import drain

    from fiber_bundle import vault_scope
    from fiber_bundle.vault_sync import maintenance as index_maintenance
    from fiber_bundle.vault_sync import store as sync

    cid, vid, root, *_ = ready
    job = interrupted(config, ready, second=False)
    sync.register(config, vid, str(root), vault_scope.Scope(), "legacy-wait")
    drain(config, vid)
    with sync.connection(config, write=True) as side:
        side.execute(
            "UPDATE libraries SET state='waiting',error='vault_sync_source_unavailable',"
            "index_after=?,index_attempts=4 WHERE id=?",
            (time.time() + 1800, vid),
        )
        assert (
            side.execute("SELECT 1 FROM sqlite_master WHERE name='index_dependencies'").fetchone()
            is None
        )
    with database.locked(config), database.connect(config.state_root) as db, ExitStack() as stack:
        token = verification.projection(config, db, job)["plan_sha256"]
        no_replay(stack)
        result = verification.apply(config, db, job, token)
        assert result["archive_verifications"][-1]["state"] == "verified"
    drain(config, vid)
    assert sync.library(config, vid)["index_after"] == 0
    assert sync.library(config, vid)["index_attempts"] == 0
    assert index_maintenance.next_task(config, {vid}, time.time()) == (vid, cid)
