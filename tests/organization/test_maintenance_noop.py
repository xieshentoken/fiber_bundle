from unittest.mock import patch

from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import database
from fiber_bundle.organization import jobs, maintenance, maintenance_undo, store


def test_unchanged_item_has_no_undo_snapshot_and_keeps_current_bytes(config, ready):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        jobs.run(config, db, jobs.submit(config, db, cid, vid))
        job = preview(config, db, cid, vid, "additive")
        assert all(not i["add"] and not i["remove"] and not i["target"] for i in job["items"])
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        before = note.read_bytes(), moc.read_bytes()
        with patch.object(maintenance_undo, "begin", wraps=maintenance_undo.begin) as capture:
            result = maintenance.run(config, db, job)
        assert result["state"] == "completed"
        assert capture.call_count == 0
        assert store.get(config, "maintenance_undo", job["id"]) is None
        assert (note.read_bytes(), moc.read_bytes()) == before


def test_noop_still_detects_source_changed_after_confirmation(config, ready):
    cid, vid, _, _, note = ready
    with database.connect(config.state_root) as db:
        jobs.run(config, db, jobs.submit(config, db, cid, vid))
        job = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        note.write_text(note.read_text() + "\nUser correction\n")
        result = maintenance.run(config, db, job)
        assert result["state"] == "partial"
        assert result["error"] == "maintenance_note_changed"
        assert note.read_text().endswith("User correction\n")


def test_noop_batches_remain_bounded_without_journals_after_first_batch(config, ready):
    cid, vid, root, moc, note = ready
    text = "---\nmoc: '[[热管理 MOC]]'\n---\n# 传热\n\n#热管理\n傅里叶定律。\n"
    note.write_text(text)
    for number in range(39):
        (root / f"Inbox/plain-{number}.md").write_text(text)
    before = {str(p): p.read_bytes() for p in root.rglob("*.md")}
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        assert len(job["items"]) == 40
        assert all(not i["add"] and not i["remove"] for i in job["items"])
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(maintenance_undo, "begin", wraps=maintenance_undo.begin) as capture:
            first = maintenance.run(config, db, job)
            assert first["state"] == "running" and first["completed"] == 32
            result = maintenance.run(config, db, job)
            assert result["state"] == "completed" and result["completed"] == 40
        assert capture.call_count == 0
        assert {str(p): p.read_bytes() for p in root.rglob("*.md")} == before
