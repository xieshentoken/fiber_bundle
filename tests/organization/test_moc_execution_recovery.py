from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import (
    cloud_observation,
    database,
    moc_file_journal,
    moc_files,
    moc_registration,
    moc_store,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.organization import execution, maintenance, store


@pytest.mark.parametrize("human_edit", [False, True])
def test_restart_recovers_relation_committed_file_rolled_back_only_explicitly(
    config, ready, human_edit
):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        from fiber_bundle.objects import digest

        registration = moc_registration.preview(
            config, db, cid, vid, "Map/热管理 MOC.md", digest(moc.read_bytes())
        )
        moc_registration.apply(config, db, cid, vid, registration["operation_id"], "apply")
        job = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        before = moc.read_bytes()
        original = moc_files.context

        def changed(*args, **kwargs):
            if kwargs.get("allow_after"):
                raise cloud_observation.ObservationError(
                    "cloud_item_pending_stale_or_conflicted", moc, "stale"
                )
            return original(*args, **kwargs)

        with patch.object(moc_files, "context", side_effect=changed):
            failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial"
        assert failed["error_detail"]["path"] == "Map/热管理 MOC.md"
        assert failed["error_detail"]["phase"] == "moc_apply"
        # A later index failure must not consume the explicit resume of unfinished work.
        failed.update(index_state="failed", index_error="note_index_update_required")
        store.put(config, "jobs", job["id"], vid, failed)
        mid = job["items"][0]["add"][0]
        record = moc_store.load(config, vid, mid)
        assert job["items"][0]["entity"] in record.members
        assert moc.read_bytes() == before
        assert any(p.status == "not-applied" for p in moc_file_journal.plans(config, vid, mid))
        revision = record.revision
    saved = store.get(config, "jobs", job["id"])
    execution.run(config, job["id"])
    assert store.get(config, "jobs", job["id"]) == saved  # Startup does not resume partial tasks.
    if human_edit:
        moc.write_bytes(before + b"\nHuman correction\n")
    with database.connect(config.state_root) as db:
        restored = store.get(config, "jobs", job["id"])
        maintenance.action(config, db, restored, "resume", None)
        assert restored["state"] == "queued"
        assert not restored["error"] and "error_detail" not in restored
        result = maintenance.run(config, db, restored)
        if human_edit:
            assert result["state"] == "partial"
            assert result["error"] == "maintenance_moc_changed"
            assert result["recovery_error"]["error"] == "maintenance_undo_file_conflict"
            assert moc.read_bytes() == before + b"\nHuman correction\n"
        else:
            assert result["state"] == "completed", result
            assert moc_store.load(config, vid, mid).revision == revision
            assert moc.read_text().count("../Inbox/") == 1
            assert note.read_text().count("[[Map/热管理 MOC]]") == 1
            finished = moc.read_bytes(), note.read_bytes()
            with pytest.raises(InvalidInput):
                maintenance.action(config, db, result, "resume", None)
            assert (moc.read_bytes(), note.read_bytes()) == finished
