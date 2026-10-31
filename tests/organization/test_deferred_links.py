"""An explicit incomplete archive and later current-content CAS repair, on synthetic files."""

import json
from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_partial_apply import finish, unavailable

from fiber_bundle import database, note_index
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import jobs, maintenance, packages, store
from fiber_bundle.organization import maintenance_link_repair as repair


def archive(config, ready):
    cid, vid, root, _, _ = ready
    bad = root / "Inbox/edited.md"
    bad.write_text("# Current human text\n[[Inbox/传热#章节|原笔记]]\n")
    with (
        database.connect(config.state_root) as db,
        patch.object(note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")),
    ):
        job = preview(config, db, cid, vid, "quick", unavailable_action="defer_links")
        assert job["items"][0]["target"] == "Resource/传热.md"
        assert maintenance.public(job)["application_blocked"] is None
        finish(config, db, job)
        assert job["items"][0]["links_pending"]
        row = db.execute(
            "SELECT payload FROM vault_operations WHERE id=?",
            (job["items"][0]["archive_operation"],),
        ).fetchone()
        assert json.loads(row[0])["deferred_links"]["state"] == "pending"
    assert (root / "Resource/传热.md").exists()
    assert "Inbox/传热" in bad.read_text()
    return job, bad


def test_archive_and_current_body_repair_requires_confirmation_and_is_idempotent(config, ready):
    job, bad = archive(config, ready)
    original = bad.read_text() + "\n用户后续纠正，必须保留。\n"
    bad.write_text(original)
    with database.connect(config.state_root) as db:
        p = repair.preview(config, db, job)
        assert not p["file_write_performed"] and len(p["changes"]) == 1
        assert bad.read_text() == original
        assert "Resource/传热" in p["changes"][0]["after"]
        assert "#章节|原笔记" in p["changes"][0]["after"]
        with pytest.raises(IntegrityError):
            repair.apply(config, db, job, p["operation_id"], "0" * 64)
        done = repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert done["state"] == "completed"
        after = bad.read_bytes()
        repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert bad.read_bytes() == after
        assert "用户后续纠正" in bad.read_text()
        assert not maintenance.public(job)["links_pending"]
        # Repair has separate confirmation; parent undo cannot silently undo it.
        with pytest.raises(IntegrityError, match="link_repair_completed"):
            maintenance.action(config, db, job, "cancel", None)


def test_unreadable_repair_waits_and_edit_after_preview_is_not_overwritten(config, ready):
    job, bad = archive(config, ready)
    with database.connect(config.state_root) as db:
        with patch.object(
            note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")
        ):
            p = repair.preview(config, db, job)
            assert p["remaining"] == ["Inbox/edited.md"] and not p["changes"]
            with pytest.raises(InvalidInput, match="not_ready"):
                repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        p = repair.preview(config, db, job)
        bad.write_text(bad.read_text() + "\n编辑竞态\n")
        with pytest.raises(IntegrityError, match="source_changed"):
            repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert "编辑竞态" in bad.read_text() and "Inbox/传热" in bad.read_text()


def test_reused_old_path_is_not_reinterpreted(config, ready):
    job, bad = archive(config, ready)
    (ready[2] / "Inbox/传热.md").write_text("# Completely different note\n")
    with database.connect(config.state_root) as db:
        with pytest.raises(IntegrityError, match="old_path_reused"):
            repair.preview(config, db, job)
    assert "Inbox/传热" in bad.read_text()


def test_deferred_native_draft_is_moved_intact(config, ready, package):
    cid, vid, root, _, _ = ready
    package.rename(root / "Inbox/fake-thermal")
    before = {
        p.relative_to(root / "Inbox/fake-thermal").as_posix(): p.read_bytes()
        for p in (root / "Inbox/fake-thermal").rglob("*")
        if p.is_file()
    }
    (root / "Inbox/edited.md").write_text("# Pending body\n[[Inbox/fake-thermal/SKILL]]\n")
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Inbox/fake-thermal")
        policy = jobs.policy(config, vid)
        _, mocs, _ = jobs.snapshot(config, db, cid, vid)
        jobs.save_policy(
            config,
            db,
            cid,
            vid,
            {
                **policy["settings"],
                "rules": [
                    {
                        "moc_id": mocs[0]["id"],
                        "match": "all",
                        "conditions": [{"field": "kind", "values": ["expert"]}],
                    }
                ],
            },
            policy["revision"],
        )
        with patch.object(
            note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")
        ):
            job = preview(config, db, cid, vid, "quick", unavailable_action="defer_links")
            finish(config, db, job)
        assert all(
            (root / "Book/fake-thermal" / p).read_bytes() == data for p, data in before.items()
        )
        p = repair.preview(config, db, job)
        repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert "Book/fake-thermal/SKILL" in (root / "Inbox/edited.md").read_text()
        assert all(
            (root / "Book/fake-thermal" / p).read_bytes() == data for p, data in before.items()
        )


def test_repair_resumes_after_exchange_crash(config, ready, monkeypatch):
    job, bad = archive(config, ready)
    with database.connect(config.state_root) as db:
        p = repair.preview(config, db, job)
        original = repair.execution.complete

        def crash(*args, **kwargs):
            raise RuntimeError("simulated power loss")

        monkeypatch.setattr(repair.execution, "complete", crash)
        with pytest.raises(RuntimeError):
            repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert "Resource/传热" in bad.read_text()
        monkeypatch.setattr(repair.execution, "complete", original)
        repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert not store.get(config, "jobs", job["id"])["items"][0]["links_pending"]


def test_cancel_archive_before_link_repair_restores_only_owned_files(config, ready):
    job, bad = archive(config, ready)
    before = bad.read_bytes()
    with database.connect(config.state_root) as db:
        maintenance.action(config, db, job, "cancel", None)
        for _ in range(5):
            job = maintenance.run(config, db, job)
            if job["state"] in {"cancelled", "rollback_conflict"}:
                break
        assert job["state"] == "cancelled", job.get("error")
        assert ready[4].exists() and not (ready[2] / "Resource/传热.md").exists()
        assert bad.read_bytes() == before
        with pytest.raises(IntegrityError, match="task_not_ready"):
            repair.preview(config, db, job)


def test_choice_is_sealed_and_http_repair_is_scoped(http, config, ready):
    cid, vid, root, *_ = ready
    (root / "Inbox/edited.md").write_text("# Pending\n")
    with (
        database.connect(config.state_root) as db,
        patch.object(note_index, "read_note", unavailable(note_index.read_note, "Inbox/edited.md")),
    ):
        job = preview(config, db, cid, vid, "quick")
        job["fixed"]["unavailable_action"] = "defer_links"
        with pytest.raises(InvalidInput, match="confirmation_required"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    denied = http.post(
        "/admin/organization/maintenance",
        json={
            "client_id": cid,
            "vault_id": vid,
            "mode": "quick",
            "unavailable_action": "silently_ignore",
        },
    )
    assert denied.status_code == 422


def test_resume_repair_retains_later_edit_instead_of_replaying_old_bytes(
    config, ready, monkeypatch
):
    job, bad = archive(config, ready)
    with database.connect(config.state_root) as db:
        p = repair.preview(config, db, job)
        original = repair.execution.complete
        monkeypatch.setattr(
            repair.execution, "complete", lambda *args: (_ for _ in ()).throw(RuntimeError("crash"))
        )
        with pytest.raises(RuntimeError):
            repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        later = bad.read_text() + "\nUpdated while recovering\n"
        bad.write_text(later)
        monkeypatch.setattr(repair.execution, "complete", original)
        recovered = repair.preview(config, db, job)
        assert recovered["operation_id"] == p["operation_id"]
        with pytest.raises(IntegrityError):
            repair.apply(config, db, job, p["operation_id"], p["plan_sha256"])
        assert bad.read_text() == later
