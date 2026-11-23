"""Normal resume converges the original archive before validating shared MOC consumers."""

import json
import shutil
import time
from copy import deepcopy
from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import (
    auth,
    cloud_observation,
    database,
    moc_file_journal,
    moc_placement_projection,
    note_placement_execution,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import (
    jobs,
    maintenance,
    maintenance_archive,
    membership,
    store,
)


def shared(config, db, ready, maps=1):
    cid, vid, root, _, _ = ready
    (root / "Inbox/第二篇.md").write_text("# 第二篇\n#热管理\n保持原内容与身份。\n")
    if maps == 2:
        (root / "Map/材料 MOC.md").write_text("---\ntype: moc\n---\n# 材料 MOC\n人工正文。\n")
    entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
    for entity in entities:
        for moc in mocs:
            membership.apply(config, db, cid, vid, entity, moc, owner="manual")
    job = preview(config, db, cid, vid, "quick")
    assert len(job["items"]) == 2
    assert all(not i["add"] and len(i["after"]) == maps for i in job["items"])
    maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    return job


@pytest.mark.parametrize("crash", ["metadata", "journal", "first_map", "job_save"])
@pytest.mark.parametrize("maps", [1, 2])
def test_resume_finishes_owner_before_shared_item_validation(config, ready, crash, maps):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready, maps)
        finalizer = moc_placement_projection.finalize

        def interrupted(*args):
            if crash == "journal":
                finalizer(*args)
            raise OSError("synthetic finalization crash")

        if crash == "job_save":
            failing = patch.object(
                maintenance_archive, "refresh_expected", side_effect=OSError("job save crash")
            )
        elif crash == "first_map":
            record = moc_file_journal.record_applied

            def first_map(*args):
                record(*args)
                raise OSError("after first Map journal")

            failing = patch.object(moc_file_journal, "record_applied", first_map)
        else:
            failing = patch.object(moc_placement_projection, "finalize", interrupted)
        with failing:
            failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial", failed
        owner = next(i for i in job["items"] if i.get("archive_operation"))
        other = next(i for i in job["items"] if not i.get("archive_operation"))
        # The original failure occurs even when this other shared-Map item is first.
        job["items"] = [other, owner]
        store.put(config, "jobs", job["id"], vid, job)
        before = {
            p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_ino)
            for p in root.rglob("*.md")
        }
        original_operation = owner["archive_operation"]
    with database.connect(config.state_root) as db:
        job = store.get(config, "jobs", job["id"])
        with (
            patch.object(
                note_placement_execution, "exchange", side_effect=AssertionError("exchange replay")
            ),
            patch.object(
                note_placement_execution, "move", side_effect=AssertionError("move replay")
            ),
            patch.object(
                note_placement_execution, "complete", side_effect=AssertionError("metadata replay")
            ),
        ):
            maintenance.action(config, db, job, "resume", None)
        assert (
            db.execute(
                "SELECT state FROM vault_operations WHERE id=?", (original_operation,)
            ).fetchone()[0]
            == "completed"
        )
        assert before == {
            p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_ino)
            for p in root.rglob("*.md")
        }
        assert next(i for i in job["items"] if i.get("archive_operation"))["phase"] == "archived"
        # A second pause/resume and a restarted worker keep the same operation.
        maintenance.action(config, db, job, "pause", None)
        maintenance.action(config, db, job, "resume", None)
        for _ in range(3):
            job = maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        assert job["completed"] == 2
        assert original_operation in [i.get("archive_operation") for i in job["items"]]
        assert (
            db.execute(
                "SELECT count(*) FROM vault_operations WHERE kind='maintenance_archive'"
            ).fetchone()[0]
            == 2
        )
        assert (root / "Resource/第二篇.md").exists()


def test_resume_uses_owned_projection_chain_when_job_expected_save_lags_multiple_archives(
    config, ready
):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready, maps=2)
        expected = deepcopy(job["moc_expected"])
        for _ in range(2):
            job = maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        operations = [i["archive_operation"] for i in job["items"]]
        job.update(state="partial", error="synthetic stale job snapshot", moc_expected=expected)
        store.put(config, "jobs", job["id"], vid, job)
        before = {p: (p.read_bytes(), p.stat().st_ino) for p in root.rglob("*.md")}
    with database.connect(config.state_root) as db:
        job = store.get(config, "jobs", job["id"])
        with (
            patch.object(
                note_placement_execution, "exchange", side_effect=AssertionError("replay")
            ),
            patch.object(note_placement_execution, "move", side_effect=AssertionError("replay")),
            patch.object(
                note_placement_execution, "complete", side_effect=AssertionError("replay")
            ),
        ):
            maintenance.action(config, db, job, "resume", None)
            assert maintenance.run(config, db, job)["state"] == "completed"
        assert [i["archive_operation"] for i in job["items"]] == operations
        assert all((p.read_bytes(), p.stat().st_ino) == evidence for p, evidence in before.items())


def test_resume_cannot_adopt_manual_change_after_own_committed_projection(config, ready):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready)
        with patch.object(
            maintenance_archive, "refresh_expected", side_effect=OSError("job save crash")
        ):
            maintenance.run(config, db, job)
        frozen = deepcopy(job["moc_expected"])
        moc.write_text(moc.read_text() + "\nHuman edit after archive\n")
        before = {p: p.read_bytes() for p in root.rglob("*.md")}
        with pytest.raises(IntegrityError, match="maintenance_membership_baseline_changed"):
            maintenance.action(config, db, job, "resume", None)
        assert job["moc_expected"] == frozen
        assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("change", ["edit", "replace", "revoke", "root"])
def test_committed_resume_rejects_external_change_before_any_replayed_write(config, ready, change):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready)
        with patch.object(moc_placement_projection, "finalize", side_effect=OSError("crash")):
            maintenance.run(config, db, job)
        if change == "edit":
            moc.write_text(moc.read_text() + "\nHuman changed this Map\n")
        elif change == "replace":
            replacement = root / ".external-save"
            replacement.write_bytes(moc.read_bytes())
            replacement.replace(moc)
        elif change == "revoke":
            auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        else:
            saved = root.with_name(root.name + "-original")
            root.rename(saved)
            shutil.copytree(saved, root)
        frozen = deepcopy(job["moc_expected"])
        with (
            patch.object(
                note_placement_execution, "exchange", side_effect=AssertionError("replay")
            ),
            patch.object(note_placement_execution, "move", side_effect=AssertionError("replay")),
            patch.object(
                note_placement_execution, "complete", side_effect=AssertionError("replay")
            ),
            pytest.raises((IntegrityError, auth.Denied)) as caught,
        ):
            maintenance.action(config, db, job, "resume", None)
        assert job["moc_expected"] == frozen
        if change in {"edit", "replace"}:
            assert isinstance(caught.value, cloud_observation.ObservationError)
            assert caught.value.detail["path"] == "Map/热管理 MOC.md"
            assert caught.value.detail["phase"] == "archive_committed_verify"
            assert caught.value.detail["changed_fields"] == [
                "sha256" if change == "edit" else "inode"
            ]
            assert not caught.value.detail["retryable_read"]


def test_expired_operation_can_only_resume_after_original_metadata_committed(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready)
        with patch.object(moc_placement_projection, "finalize", side_effect=OSError("crash")):
            maintenance.run(config, db, job)
        item = next(i for i in job["items"] if i.get("archive_operation"))
        row = db.execute(
            "SELECT payload FROM vault_operations WHERE id=?", (item["archive_operation"],)
        ).fetchone()
        value = json.loads(row[0])
        value["expires_at"] = time.time() - 3600
        value["metadata_committed"] = False
        note_placement_execution.persist(db, item["archive_operation"], value, "reconcile")
        with pytest.raises(InvalidInput, match="maintenance_archive_deferral_review_required"):
            maintenance.action(config, db, job, "resume", None)
        value["metadata_committed"] = True
        note_placement_execution.persist(db, item["archive_operation"], value, "reconcile")
        with (
            patch.object(
                note_placement_execution, "exchange", side_effect=AssertionError("replay")
            ),
            patch.object(note_placement_execution, "move", side_effect=AssertionError("replay")),
            patch.object(
                note_placement_execution, "complete", side_effect=AssertionError("replay")
            ),
        ):
            maintenance.action(config, db, job, "resume", None)
        assert job["state"] == "queued"


def test_resumed_finalization_keeps_original_undo_and_manual_relations(config, ready):
    _, _, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready, maps=2)
        before = {p: (p.read_bytes(), p.stat().st_ino) for p in root.rglob("*.md")}
        original = moc_placement_projection.finalize

        def interrupted(*args):
            original(*args)
            raise OSError("after original projection journal")

        with patch.object(moc_placement_projection, "finalize", interrupted):
            maintenance.run(config, db, job)
        maintenance.action(config, db, job, "resume", None)
        for _ in range(3):
            maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        maintenance.action(config, db, job, "cancel", None)
        for _ in range(12):
            maintenance.run(config, db, job)
            if job["state"] == "cancelled":
                break
        assert job["state"] == "cancelled", job
        assert set(root.rglob("*.md")) == set(before)
        assert all((p.read_bytes(), p.stat().st_ino) == evidence for p, evidence in before.items())
