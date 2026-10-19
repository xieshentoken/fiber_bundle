"""Observable Map/property consistency across interrupted classification writes."""

import copy

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_maintenance_apply import execute

from fiber_bundle import database, moc_file_journal, moc_files, moc_markdown, moc_store
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import (
    jobs,
    maintenance,
    membership,
    note_moc_fields,
    note_moc_sync,
    store,
)


def historical_map(config, db, ready):
    cid, vid, root, moc, _ = ready
    old = root / "Resource/旧笔记.md"
    old.write_text("# 旧笔记\n人工保留内容\n")
    entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
    source = next(e for e in entities if e["path"] == "Resource/旧笔记.md")
    membership.apply(config, db, cid, vid, source, mocs[0], owner="manual")
    mid = mocs[0]["id"]
    prefix, _, suffix = moc_markdown.split(moc.read_bytes(), mid)
    moc.write_bytes(prefix + suffix)
    return mid, source["id"], moc.read_bytes()


def test_missing_historical_block_rebuilt_preserving_human_bytes_and_members(config, ready):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        mid, human_id, human = historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        saved = copy.deepcopy(job["items"][0])
        result = execute(config, db, job)
        assert result["state"] == "completed", result
        assert moc.read_bytes().startswith(human)
        prefix, block, suffix = moc_markdown.split(moc.read_bytes(), mid)
        assert block and "旧笔记" in block.decode() and "传热" in block.decode()
        assert prefix + suffix == human
        record = moc_store.load(config, vid, mid)
        assert record.members[human_id] == ["manual"]
        assert record.members[saved["entity"]] == ["system"]
        assert note_moc_fields.field((root / "Resource/传热.md").read_text())[2] == [
            "[[Map/热管理 MOC]]"
        ]
        assert result["items"][0]["fingerprint"] == saved["fingerprint"]
        assert result["items"][0]["selected"] == saved["selected"]
        assert not result["model_called"]
        assert len(moc_file_journal.plans(config, vid, mid)) >= 2
        assert not maintenance.submit(config, db, cid, vid, "quick")["items"]


def test_general_writer_still_refuses_missing_block_without_bound_repair(config, ready):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        mid, _, _ = historical_map(config, db, ready)
        record = moc_store.load(config, vid, mid)
        with pytest.raises(IntegrityError, match="managed block missing"):
            moc_files.plan_file(
                config,
                db,
                cid,
                vid,
                mid,
                record.revision,
                "Map/热管理 MOC.md",
                __import__("hashlib").sha256(moc.read_bytes()).hexdigest(),
            )


def test_map_change_after_confirmation_is_not_repaired_or_overwritten(config, ready):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        mid, _, _ = historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        before_record = moc_store.load(config, vid, mid)
        human = moc.read_bytes() + "\n新的人工补充\n".encode()
        moc.write_bytes(human)
        result = maintenance.run(config, db, job)
        assert result["state"] == "partial" and result["error"] == "maintenance_moc_changed"
        assert moc.read_bytes() == human
        assert moc_store.load(config, vid, mid) == before_record
        assert "moc:" not in note.read_text()


def test_repair_proof_cannot_authorize_a_later_file_change(config, ready, monkeypatch):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        original = membership.project
        rejected = []

        def changed(*args, **kwargs):
            moc.write_bytes(moc.read_bytes() + "\n保留并发修改\n".encode())
            try:
                return original(*args, **kwargs)
            except IntegrityError as error:
                rejected.append(str(error))
                raise

        monkeypatch.setattr(membership, "project", changed)
        result = maintenance.run(config, db, job)
        assert result["state"] == "partial"
        assert rejected == ["MOC managed block missing or human edited"]
        assert result["error"] == "maintenance_undo_file_conflict"
        assert "保留并发修改" in moc.read_text() and b"<!-- fiber-moc:" not in moc.read_bytes()


def test_edited_block_rejected_before_any_new_relation(config, ready):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        historical_map(config, db, ready)
        # Restore once, then simulate a genuine manual edit inside the block.
        job = preview(config, db, cid, vid, "quick")
        job = execute(config, db, job)
        assert job["state"] == "completed"
        mid = job["items"][0]["after"][0]
        moc.write_text(moc.read_text().replace("系统增量关联", "人工修改的系统区块"))
        (ready[2] / "Inbox/第二篇.md").write_text("# 第二篇\n#热管理\n")
        fresh = preview(config, db, cid, vid, "quick")
        before = moc_store.load(config, vid, mid)
        with pytest.raises(IntegrityError, match="maintenance_moc_managed_block_edited"):
            maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
        assert moc_store.load(config, vid, mid) == before
        assert "人工修改的系统区块" in moc.read_text()


@pytest.mark.parametrize("point", ["metadata", "map", "exchange", "property"])
def test_resume_finishes_remaining_projection_without_duplicate_relation(
    config, ready, monkeypatch, point
):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        mid, human_id, human = historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        if point == "metadata":
            target, name = membership, "project"
        elif point == "exchange":
            target, name = moc_file_journal, "finish"
        else:
            target, name = note_moc_sync, "sync"
        original = getattr(target, name)

        def interrupted(*args, **kwargs):
            if point == "property":
                original(*args, **kwargs)
            raise OSError("interrupted after " + point)

        monkeypatch.setattr(target, name, interrupted)
        failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial", failed
        record = moc_store.load(config, vid, mid)
        assert record.members[job["items"][0]["entity"]] == ["system"]
        # The live legacy task predates the repair evidence field.
        if point == "metadata":
            failed.pop("moc_repairs", None)
        monkeypatch.setattr(target, name, original)
        maintenance.action(config, db, failed, "pause", None)
        resumed = maintenance.action(config, db, failed, "resume", None)
        done = maintenance.run(config, db, store.get(config, "jobs", resumed["id"]))
        assert done["state"] == "completed", done
        assert moc_store.load(config, vid, mid).revision == record.revision
        assert moc_store.load(config, vid, mid).members[human_id] == ["manual"]
        assert "传热" in moc.read_text() and moc.read_bytes().startswith(human)
        assert note_moc_fields.field((root / "Resource/传热.md").read_text())[2] == [
            "[[Map/热管理 MOC]]"
        ]


def test_cancel_after_repair_returns_task_owned_files_and_keeps_manual_relationship(config, ready):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        mid, human_id, human = historical_map(config, db, ready)
        note_before = note.read_bytes()
        job = execute(config, db, preview(config, db, cid, vid, "quick"))
        assert job["state"] == "completed"
        maintenance.action(config, db, job, "cancel", None)
        for _ in range(10):
            job = maintenance.run(config, db, job)
            if job["state"] == "cancelled":
                break
        assert job["state"] == "cancelled", job
        assert moc.read_bytes() == human
        assert note.read_bytes() == note_before
        assert moc_store.load(config, vid, mid).members == {human_id: ["manual"]}
