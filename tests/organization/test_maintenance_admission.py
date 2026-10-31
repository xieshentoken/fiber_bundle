from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_maintenance_undo import start

from fiber_bundle import database
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import maintenance, maintenance_admission, maintenance_undo, store


def test_background_writer_waits_without_model_calls_and_only_its_queue_resumes(config, ready):
    from fiber_bundle.organization import jobs, processing

    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        background = jobs.submit(config, db, cid, vid, "Inbox", source="automatic")
        manual = start(config, db, ready)
        maintenance.run(config, db, manual)
        maintenance.action(config, db, manual, "pause", None)
        manual["index_state"] = "ready"  # The test exercises admission, not the index worker.
        store.put(config, "jobs", manual["id"], vid, manual)
    with patch.object(processing, "prepare", side_effect=AssertionError("blocked model call")):
        jobs.tick(config)
    queued = store.get(config, "jobs", background["id"])
    assert queued["state"] == "waiting_task", queued
    assert queued["blocking_task"]["id"] == manual["id"]
    with database.connect(config.state_root) as db:
        evidence = maintenance_admission.close_evidence(config, db, manual)
        maintenance.action(config, db, manual, "finish", evidence["plan_sha256"])
    maintenance_admission.release_background_waits(config)
    assert store.get(config, "jobs", background["id"])["state"] == "queued"
    assert store.get(config, "jobs", manual["id"])["state"] == "closed"


def test_confirm_points_to_existing_task_before_creating_second_writer(config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        first = start(config, db, ready)
        maintenance.run(config, db, first)
        maintenance.action(config, db, first, "pause", None)
        fresh = preview(config, db, cid, vid, "quick")
        files = {p: p.read_bytes() for p in root.rglob("*.md")}
        result = maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
        assert result["state"] == "preview" and not result["confirmed"]
        assert result["blocking_task"]["id"] == first["id"]
        assert not store.get(config, "maintenance_undo", fresh["id"])
        assert {p: p.read_bytes() for p in root.rglob("*.md")} == files


def test_closing_http_requires_preview_digest_and_never_undoes_files(http, config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        maintenance.run(config, db, job)
        maintenance.action(config, db, job, "pause", None)
    original = {p: p.read_bytes() for p in root.rglob("*.md")}
    scope = {"client_id": cid, "vault_id": vid}
    url = "/admin/organization/maintenance/" + job["id"]
    packet = http.post(url + "/finish_preview", json=scope)
    assert packet.status_code == 200, packet.text
    token = packet.json()["close_preview"]["plan_sha256"]
    assert http.post(url + "/finish", json=scope).status_code == 422
    result = http.post(url + "/finish", json={**scope, "plan_sha256": token})
    assert result.status_code == 200 and result.json()["state"] == "closed", result.text
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == original


def test_progress_admission_does_not_deserialize_large_undo_snapshots(config, ready):
    from fiber_bundle.organization import overview

    cid, vid, _, *_ = ready
    with database.connect(config.state_root) as db:
        first = start(config, db, ready)
        maintenance.run(config, db, first)
        maintenance.action(config, db, first, "pause", None)
        preview(config, db, cid, vid, "quick")
        original = store.get

        def get(config, table, identity):
            assert table != "maintenance_undo", "progress must only read journal existence"
            return original(config, table, identity)

        with patch.object(store, "get", get):
            result = overview.progress(config, db, cid, vid)
        assert any(
            j.get("blocking_task", {}).get("id") == first["id"]
            for j in result["jobs"]
            if j.get("blocking_task")
        )


def test_queued_confirmation_reserves_vault_and_repeat_is_idempotent(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        first = preview(config, db, cid, vid, "quick")
        other = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, first, "confirm", first["plan_sha256"])
        maintenance.action(config, db, other, "confirm", other["plan_sha256"])
        assert other["state"] == "preview"
        assert other["blocking_task"]["id"] == first["id"]
        assert maintenance.action(config, db, first, "confirm", first["plan_sha256"]) == first


def test_close_preserves_applied_files_and_history_then_admits_new_task(config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        first = start(config, db, ready)
        maintenance.run(config, db, first)
        maintenance.action(config, db, first, "pause", None)
        files = {p: p.read_bytes() for p in root.rglob("*.md")}
        journal = store.get(config, "maintenance_undo", first["id"])
        packet = maintenance.action(config, db, first, "finish_preview", None)
        token = packet["close_preview"]["plan_sha256"]
        with pytest.raises(InvalidInput, match="confirmation_required"):
            maintenance.action(config, db, first, "finish", "0" * 64)
        result = maintenance.action(config, db, first, "finish", token)
        assert result["state"] == "closed" and result["completed"] == 1
        assert maintenance.action(config, db, result, "finish", token) == result
        assert store.get(config, "maintenance_undo", first["id"]) == journal
        assert {p: p.read_bytes() for p in root.rglob("*.md")} == files
        fresh = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
        assert fresh["state"] == "queued" and fresh["confirmed"]
        with pytest.raises(InvalidInput, match="not_resumable"):
            maintenance.action(config, db, result, "resume", None)


def test_close_refuses_unverified_intent_without_reconciling_files(config, ready):
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        job.update(state="partial")
        job["items"][0]["intent"] = "link:unknown"
        with patch.object(maintenance_undo, "seal", side_effect=AssertionError("no writes")):
            with pytest.raises(IntegrityError, match="close_recovery_required"):
                maintenance_admission.close_evidence(config, db, job)


def test_legacy_zero_write_jobs_wait_for_original_and_can_close_without_undo(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        first = start(config, db, ready)
        maintenance.run(config, db, first)
        maintenance.action(config, db, first, "pause", None)
        newer = preview(config, db, cid, vid, "quick")
        newer.update(
            state="partial",
            confirmed=True,
            stage="applying",
            error="IntegrityError: maintenance_other_task_unfinished",
        )
        store.put(config, "jobs", newer["id"], vid, newer)
        maintenance.action(config, db, newer, "resume", None)
        assert newer["state"] == "waiting_task"
        assert newer["blocking_task"]["id"] == first["id"]
        token = maintenance_admission.close_evidence(config, db, newer)["plan_sha256"]
        maintenance.action(config, db, newer, "finish", token)
        maintenance.action(config, db, first, "resume", None)
        assert first["state"] == "queued"
        maintenance.run(config, db, first)
        assert first["state"] == "completed"


def test_close_after_verified_move_crash_reads_evidence_without_replaying(config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        with patch.object(maintenance_undo, "seal", side_effect=SystemExit("crash")):
            with pytest.raises(SystemExit):
                maintenance.run(config, db, job)
        job = store.get(config, "jobs", job["id"])
        maintenance.action(config, db, job, "pause", None)
        before = {p: (p.read_bytes(), p.stat().st_ino) for p in root.rglob("*.md")}
        evidence = maintenance_admission.close_evidence(config, db, job)
        maintenance.action(config, db, job, "finish", evidence["plan_sha256"])
        assert job["state"] == "closed"
        assert {p: (p.read_bytes(), p.stat().st_ino) for p in root.rglob("*.md")} == before
