"""Real synthetic writes with an unavailable ordinary source and current-content checks."""

from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import database, moc_store, note_index
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import maintenance, maintenance_plan


def unavailable(reader, name):
    def load(root, path, limit, **kwargs):
        if path == name:
            raise IntegrityError("cloud_item_not_downloaded")
        return reader(root, path, limit, **kwargs)

    return load


def finish(config, db, job):
    maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    for _ in range(len(job["items"]) + 2):
        job = maintenance.run(config, db, job)
        if job["state"] in {"partial", "completed"}:
            break
    assert job["state"] == "completed", job.get("error")
    return job


@pytest.mark.parametrize("mode", ["quick", "additive", "deep"])
def test_mount_healthy_note_defer_move_preserve_omitted(config, ready, mode):
    cid, vid, root, moc, note = ready
    bad = root / "Inbox/edited.md"
    bad.write_text("# User edited body\nCurrent corrections remain authoritative.\n")
    original = bad.read_bytes()
    human = moc.read_bytes()
    with (
        database.connect(config.state_root) as db,
        patch.object(note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")),
    ):
        job = preview(config, db, cid, vid, mode)
        assert not job["items"][0]["target"]
        if mode == "quick":
            assert job["items"][0]["deferred_target"] == "Resource/传热.md"
        finish(config, db, job)
        record = moc_store.load(config, vid, job["mocs"][0]["id"])
        assert job["items"][0]["entity"] in record.members
    assert bad.read_bytes() == original
    assert note.exists() and not (root / "Resource/传热.md").exists()
    assert moc.read_bytes().startswith(human)
    assert "传热.md" in moc.read_text()


def test_old_partial_archive_contract_remains_blocked(config, ready):
    cid, vid, root, *_ = ready
    (root / "Inbox/waiting.md").write_text("# wait\n")
    with (
        database.connect(config.state_root) as db,
        patch.object(
            note_index, "read_note", unavailable(note_index.read_note, "Inbox/waiting.md")
        ),
    ):
        job = preview(config, db, cid, vid, "quick")
        del job["fixed"]["unavailable_action"]
        item = job["items"][0]
        item.pop("defer_archive")
        item.pop("deferred_target")
        maintenance_plan.difference(item, "quick", job["fixed"]["save_policy"]["settings"])
        # Regenerate the legacy fingerprint/seal, representing a pre-update preview.
        es, ms, fixed = maintenance_plan.snapshot(config, db, cid, vid, "quick")
        job["items"] = [maintenance_plan.item(config, vid, e, ms, fixed, "quick") for e in es]
        job["plan_sha256"] = maintenance_plan.seal(job)
        with pytest.raises(IntegrityError, match="archive_scope_incomplete"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert not job["confirmed"]


def test_existing_unreadable_member_link_and_manual_prose_survive(config, ready):
    cid, vid, root, moc, _ = ready
    edited = root / "Inbox/edited.md"
    edited.write_text("# 手动修正\n#热管理\n当前准确内容\n")
    with database.connect(config.state_root) as db:
        finish(config, db, preview(config, db, cid, vid, "additive"))
        before = moc.read_text()
        retained = next(line for line in before.splitlines() if "edited.md" in line)
        manual = "\n人工补充，不属于系统区。\n"
        moc.write_text(before + manual)
        (root / "Inbox/new.md").write_text("# 新传热\n#热管理\n新内容\n")
        original = edited.read_bytes()
        with patch.object(
            note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")
        ):
            job = preview(config, db, cid, vid, "quick")
            finish(config, db, job)
        assert moc.read_text().count(retained) == 1
        assert moc.read_text().endswith(manual)
        assert edited.read_bytes() == original


def test_partial_new_moc_and_recovery_require_current_preview(config, ready):
    cid, vid, root, moc, note = ready
    bad = root / "Inbox/edited.md"
    bad.write_text("# 手工纠正\n#热管理\n不是原提取缓存。\n")
    with database.connect(config.state_root) as db:
        with patch.object(
            note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")
        ):
            job = preview(
                config,
                db,
                cid,
                vid,
                "quick",
                new_mocs=[{"title": "新主题", "topic": "另一个独立分类的说明"}],
            )
            finish(config, db, job)
        assert (root / "Map/新主题 MOC.md").exists()
        fresh = preview(config, db, cid, vid, "quick")
        assert any(i["title"] == "手工纠正" for i in fresh["items"])
        assert "coverage" not in fresh["fixed"]
        note.write_text(note.read_text() + "\n用户又修改了。\n")
        with pytest.raises(IntegrityError):
            maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
