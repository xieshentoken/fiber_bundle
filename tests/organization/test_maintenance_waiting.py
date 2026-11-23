"""Unavailable sources wait independently; confirmed work and current edits survive."""

from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import cloud_files, cloud_observation, database, moc_store
from fiber_bundle.organization import maintenance, maintenance_undo, store


def drain(config, db, job):
    for _ in range(len(job["items"]) + 3):
        job = maintenance.run(config, db, job)
        if job["state"] not in {"queued", "running"}:
            return job
    pytest.fail("task did not yield")


def missing(original, path):
    def check(selected, *args, **kwargs):
        if selected == path:
            raise cloud_observation.ObservationError(
                "cloud_item_not_downloaded", selected, "pending"
            )
        return original(selected, *args, **kwargs)

    return check


@pytest.mark.parametrize("strategy", ["moc_only", "defer_links"])
def test_cloud_source_waits_others_finish_and_explicit_resume_is_idempotent(
    config, ready, strategy
):
    cid, vid, root, moc, note = ready
    bad = root / "Inbox/waiting.md"
    bad.write_text("# waiting\n#热管理\nCurrent user text.\n")
    original = bad.read_bytes()
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick", unavailable_action=strategy)
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(cloud_files, "require_local", missing(cloud_files.require_local, bad)):
            job = drain(config, db, job)
        assert job["state"] == "waiting_file", job
        good = next(i for i in job["items"] if i["path"] == "Inbox/传热.md")
        waiting = next(i for i in job["items"] if i["path"] == "Inbox/waiting.md")
        assert waiting["state"] == "waiting_file" and not waiting.get("steps")
        assert good["phase"] == ("archived" if strategy == "defer_links" else "members_done")
        assert (root / "Resource/传热.md").exists() == (strategy == "defer_links")
        record = moc_store.load(config, vid, job["mocs"][0]["id"])
        assert good["entity"] in record.members and waiting["entity"] not in record.members
        assert bad.read_bytes() == original
        maintenance.action(config, db, job, "resume", None)
        job = drain(config, db, job)
        assert job["state"] == "completed", job
        assert job["completed"] == 2
        assert (root / "Resource/waiting.md").exists()
        assert (root / "Resource/传热.md").exists()
        stable = {
            p: p.read_bytes()
            for p in [moc, root / "Resource/waiting.md", root / "Resource/传热.md"]
        }
        assert maintenance.run(config, db, job)["state"] == "completed"
        assert all(p.read_bytes() == raw for p, raw in stable.items())


def test_user_edit_while_waiting_requires_new_preview_and_preserves_text(config, ready):
    cid, vid, root, _, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(cloud_files, "require_local", missing(cloud_files.require_local, note)):
            job = drain(config, db, job)
        note.write_text("# User corrected topic\nA new subject, no old model output.\n")
        current = note.read_bytes()
        maintenance.action(config, db, job, "resume", None)
        job = drain(config, db, job)
        assert job["state"] == "waiting_file", job
        assert job["items"][0]["wait"]["reason"] == "changed"
        assert note.read_bytes() == current
        assert not (root / "Resource/传热.md").exists()


def test_completed_source_not_resnapshotted_by_later_unit_but_undo_checks_it(config, ready):
    cid, vid, root, moc, note = ready
    (root / "Inbox/zz.md").write_text("# Second\n#热管理\nAnother source.\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick", unavailable_action="defer_links")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        job = maintenance.run(config, db, job)
        moved = root / next(i["target"] for i in job["items"] if i["state"] == "done")
        # Seal must not recursively revisit all prior archive bodies.
        real_observe = maintenance_undo.files.observe

        def observe(base, path):
            if base / path == moved:
                raise cloud_observation.ObservationError(
                    "cloud_item_not_downloaded", moved, "pending"
                )
            return real_observe(base, path)

        with patch.object(maintenance_undo.files, "observe", observe):
            job = drain(config, db, job)
            assert job["state"] == "completed", job
            maintenance.action(config, db, job, "cancel", None)
            job = drain(config, db, job)
        assert job["state"] == "rollback_conflict", job
        assert moved.exists()


def test_interrupted_unknown_exchange_keeps_recovery_barrier(config, ready):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(maintenance_undo, "seal", side_effect=SystemExit("crash")):
            with pytest.raises(SystemExit):
                maintenance.run(config, db, job)
        stored = store.get(config, "jobs", job["id"])
        assert stored["state"] == "running"
        assert store.get(config, "maintenance_undo", job["id"])["state"] == "applying"
        job = drain(config, db, stored)
        assert job["state"] == "completed", job
        assert (root / "Resource/传热.md").exists()
