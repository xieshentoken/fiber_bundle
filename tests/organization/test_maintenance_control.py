import copy

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import database
from fiber_bundle.config import InvalidInput
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_review,
    semantic,
    store,
)


def test_preview_pause_resume_refreshes_confirmation_without_mutations(config, ready):
    cid, vid, _, moc, note = ready
    before = [p.read_bytes() for p in [moc, note]]
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "pause", None)
        assert job["state"] == "paused"
        assert maintenance.run(config, db, job)["state"] == "paused"
        maintenance.action(config, db, job, "resume", None)
        assert job["state"] == "preview"
    assert [p.read_bytes() for p in [moc, note]] == before


def test_stale_analysis_cannot_commit_after_pause_and_resume(config, ready, monkeypatch):
    cid, vid, _, _, note = ready
    note.write_text(
        "# 无标签资料\n完整资料正文需要语义分析，两段原始引文用于分类，用户可能在期间暂停任务。\n"
    )
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})

    def resolve(*args):
        with database.connect(config.state_root) as db:
            current = store.get(config, "jobs", job["id"])
            maintenance.action(config, db, current, "pause", None)
            maintenance.action(config, db, current, "resume", None)
        return {"selected": [], "model_called": True}

    monkeypatch.setattr(model_review, "resolve", resolve)
    maintenance_analysis.prepare(config, job["id"])
    current = store.get(config, "jobs", job["id"])
    assert current["state"] == "queued" and current["generation"] == 2
    assert not current["items"][0]["evaluated"] and not current["error"]


def test_resume_only_invalidates_changed_note_preserving_previous_analysis(config, ready):
    cid, vid, root, _, note = ready
    other = root / "Inbox/另一篇.md"
    other.write_text("# 别的资料\n尚无可靠标签，不强行归类。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
    maintenance_analysis.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        job = store.get(config, "jobs", job["id"])
        kept = copy.deepcopy(next(i for i in job["items"] if i["path"].endswith("传热.md")))
        maintenance.action(config, db, job, "pause", None)
        other.write_text(other.read_text() + "修改正文内容。\n")
        maintenance.action(config, db, job, "resume", None)
        assert next(i for i in job["items"] if i["entity"] == kept["entity"]) == kept
        assert not next(i for i in job["items"] if i["path"].endswith("另一篇.md"))["evaluated"]
        assert len(job["analysis_history"]) == 1


def test_cancelled_job_is_terminal_and_new_job_allowed(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "cancel", None)
        with pytest.raises(InvalidInput, match="not_resumable"):
            maintenance.action(config, db, job, "resume", None)
        assert maintenance.submit(config, db, cid, vid, "quick")["id"] != job["id"]


def test_http_pause_resume_cancel_and_new_task_keep_original_data(http, config, ready):
    cid, vid, _, moc, note = ready
    scope = {"client_id": cid, "vault_id": vid}
    before = [p.read_bytes() for p in [moc, note]]
    first = http.post("/admin/organization/maintenance", json={**scope, "mode": "quick"})
    assert first.status_code == 200, first.text
    identity = first.json()["id"]
    for operation, state in [("pause", "paused"), ("resume", "queued"), ("cancel", "cancelled")]:
        result = http.post(f"/admin/organization/maintenance/{identity}/{operation}", json=scope)
        assert result.status_code == 200 and result.json()["state"] == state, result.text
    fresh = http.post("/admin/organization/maintenance", json={**scope, "mode": "quick"})
    assert fresh.status_code == 200 and fresh.json()["id"] != identity
    assert [p.read_bytes() for p in [moc, note]] == before
