"""A successful external repair must not be replayed by its original task."""

import copy

import pytest
from test_jobs import ready as ready

from fiber_bundle import database, moc_store
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import (
    jobs,
    maintenance,
    maintenance_analysis,
    maintenance_apply,
    maintenance_plan,
    manual_resolution,
    membership,
    model_review,
    quick_analysis,
    semantic,
    store,
)
from fiber_bundle.organization.maintenance_guard import AnalysisGuard


@pytest.fixture
def resolutions(monkeypatch):
    values = {}
    monkeypatch.setattr(manual_resolution, "records", lambda config, job: values)
    monkeypatch.setattr(manual_resolution, "resolved", lambda config, job: set(values))
    return values


def repaired(config, db, job, entity, resolutions):
    entities, mocs, _ = jobs.snapshot(config, db, job["client_id"], job["vault_id"])
    source = next(e for e in entities if e["id"] == entity)
    moc = next(m for m in mocs if "热管理" in m["title"])
    membership.apply(config, db, job["client_id"], job["vault_id"], source, moc, owner="manual")
    resolutions[entity] = {
        "moc_evidence": {moc["id"]: maintenance_apply.observe(config, db, job, moc["id"])}
    }


def exception(item, error="organization_model_evidence_invalid"):
    item.update(
        evaluated=True,
        selected=[],
        analysis_error=error,
        analysis_detail="body_quote_missing",
        phase="ready",
    )


def test_resolved_original_error_is_preserved_across_recheck_resume_and_apply(
    config, ready, resolutions, monkeypatch
):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        exception(job["items"][0])
        maintenance_plan.difference(
            job["items"][0], "quick", job["fixed"]["save_policy"]["settings"]
        )
        maintenance.make_preview(job, [], job["mocs"])
        store.put(config, "jobs", job["id"], vid, job)
        original = copy.deepcopy(job["items"])
        original_seal = maintenance_plan.seal(job)
        repaired(config, db, job, original[0]["entity"], resolutions)
        before = note.read_bytes(), moc.read_bytes()
        maintenance.action(config, db, job, "recheck", None)
        assert job["items"] == original
        maintenance.action(config, db, job, "pause", None)
        maintenance.action(config, db, job, "resume", None)
        assert job["items"] == original

    def forbidden(*args, **kwargs):
        raise AssertionError("resolved entity reached model or write path")

    monkeypatch.setattr(semantic, "recall", forbidden)
    monkeypatch.setattr(model_review, "resolve", forbidden)
    maintenance_analysis.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        job = store.get(config, "jobs", job["id"])
        assert job["state"] == "preview" and job["items"] == original
        assert maintenance_plan.seal(job) == original_seal
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        monkeypatch.setattr(membership, "apply", forbidden)
        job = maintenance.run(config, db, job)
        assert job["state"] == "completed" and job["completed"] == 1
        assert job["items"] == original
        assert store.get(config, "maintenance_undo", job["id"]) is None
        assert (note.read_bytes(), moc.read_bytes()) == before
        maintenance.action(config, db, job, "cancel", None)
        assert job["state"] == "cancelled"
        assert (note.read_bytes(), moc.read_bytes()) == before


def test_mixed_resume_keeps_resolution_and_analyzes_only_other_note(
    config, ready, resolutions, monkeypatch
):
    cid, vid, root, _, _ = ready
    (root / "Inbox/无标签.md").write_text(
        "# 无标签\n材料正文需要单独判断，不能复用其他笔记的人工分类。\n"
    )
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
        original = next(i for i in job["items"] if i["path"] == "Inbox/传热.md")
        exception(original)
        frozen = copy.deepcopy(original)
        repaired(config, db, job, original["entity"], resolutions)
        maintenance.action(config, db, job, "pause", None)
        maintenance.action(config, db, job, "resume", None)
        assert next(i for i in job["items"] if i["entity"] == original["entity"]) == frozen
    called = []
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})

    def resolve(config, job, entity, *args):
        called.append(entity["path"])
        return {"selected": [], "model_called": False}

    monkeypatch.setattr(model_review, "resolve", resolve)
    maintenance_analysis.prepare(config, job["id"])
    assert called == ["Inbox/无标签.md"]
    saved = store.get(config, "jobs", job["id"])
    assert next(i for i in saved["items"] if i["entity"] == original["entity"]) == frozen


def test_guard_accepts_exact_manual_projection_but_rejects_later_topic_edit(
    config, ready, resolutions
):
    cid, vid, _, moc, _ = ready
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
        guard = AnalysisGuard(config, job, None, job["mocs"])
        repaired(config, db, job, job["items"][0]["entity"], resolutions)
    guard()
    moc.write_text(moc.read_text() + "\n人工新增主题定义，旧预览不得自动采纳。\n")
    with pytest.raises(IntegrityError, match="moc_changed"):
        guard()


@pytest.mark.parametrize(
    ("operation", "error", "message"),
    [
        ("supplement_missing", "organization_model_cache_incomplete", "missing_item_required"),
        ("retry_unknown", "organization_model_result_unknown", "unknown_item_required"),
    ],
)
def test_explicit_retry_controls_never_reset_resolved_items(
    config, ready, resolutions, operation, error, message
):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
        exception(job["items"][0], error)
        job["cache_recovery"] = {"parent": "old", "entities": [job["items"][0]["entity"]]}
        maintenance.make_preview(job, [], job["mocs"])
        repaired(config, db, job, job["items"][0]["entity"], resolutions)
        before = copy.deepcopy(job["items"])
        with pytest.raises(InvalidInput, match=message):
            maintenance.action(config, db, job, operation, None)
        assert job["items"] == before
        assert not store.rows(config, "model_reviews", vid)


def test_confirmed_partial_resume_skips_resolved_failed_item_and_preserves_manual_link(
    config, ready, resolutions
):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        maintenance.make_preview(job, [], job["mocs"])
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        job.update(state="partial", error="organization_model_evidence_invalid")
        job["items"][0].update(state="failed", error="original failure")
        before_item = copy.deepcopy(job["items"][0])
        repaired(config, db, job, before_item["entity"], resolutions)
        before = note.read_bytes(), moc.read_bytes()
        maintenance.action(config, db, job, "resume", None)
        assert job["items"][0] == before_item
        maintenance.run(config, db, job)
        assert job["state"] == "completed" and job["completed"] == 1
        assert job["items"][0] == before_item
        assert (note.read_bytes(), moc.read_bytes()) == before
        record = moc_store.load(config, vid, job["mocs"][0]["id"])
        assert record.members[before_item["entity"]] == ["manual"]


def test_quick_local_round_keeps_resolved_unfinished_item_unchanged(
    config, ready, resolutions, monkeypatch
):
    cid, vid, root, _, _ = ready
    (root / "Inbox/无标签.md").write_text("# 无标签\n待人工补充主题。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        item = next(i for i in job["items"] if i["path"] == "Inbox/传热.md")
        exception(item)
        item["evaluated"] = False
        frozen = copy.deepcopy(item)
        repaired(config, db, job, item["entity"], resolutions)
        store.put(config, "jobs", job["id"], vid, job)

    def forbidden(*args, **kwargs):
        raise AssertionError("quick local round must not call models")

    monkeypatch.setattr(semantic, "recall", forbidden)
    monkeypatch.setattr(model_review, "resolve", forbidden)
    quick_analysis.prepare(config, job)
    saved = store.get(config, "jobs", job["id"])
    assert next(i for i in saved["items"] if i["entity"] == item["entity"]) == frozen
    assert next(i for i in saved["items"] if i["path"] == "Inbox/无标签.md")["evaluated"]


def test_confirmed_mixed_task_adopts_only_audited_manual_projection(config, ready, resolutions):
    cid, vid, root, moc, note = ready
    other = root / "Inbox/另篇传热.md"
    other.write_text("# 传热的另一篇\n#热管理\n温度梯度与热传导。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
        maintenance.make_preview(job, [], job["mocs"])
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        item = next(i for i in job["items"] if i["path"] == "Inbox/传热.md")
        item.update(state="failed", error="original item failure")
        frozen = copy.deepcopy(item)
        job.update(state="partial", error="original item failure")
        repaired(config, db, job, item["entity"], resolutions)
        manual_bytes = note.read_bytes()
        maintenance.action(config, db, job, "resume", None)
        maintenance.run(config, db, job)
        assert job["state"] == "completed", job.get("error")
        assert job["completed"] == 2 and item == frozen
        assert note.read_bytes() == manual_bytes
        record = moc_store.load(config, vid, job["mocs"][0]["id"])
        assert record.members[item["entity"]] == ["manual"]
        assert set(record.members) == {i["entity"] for i in job["items"]}
        assert note.name in moc.read_text() and other.name in moc.read_text()
        maintenance.action(config, db, job, "cancel", None)
        for _ in range(10):
            maintenance.run(config, db, job)
            if job["state"] in {"cancelled", "rollback_conflict"}:
                break
        assert job["state"] == "cancelled", job.get("error")
        record = moc_store.load(config, vid, job["mocs"][0]["id"])
        assert record.members == {item["entity"]: ["manual"]}
        assert note.read_bytes() == manual_bytes


def test_last_manual_resolution_finishes_existing_journal_without_reopening_it(
    config, ready, resolutions
):
    cid, vid, root, moc, _ = ready
    second = root / "Inbox/后续传热.md"
    second.write_text("# 后续传热\n#热管理\n热传导与温度梯度的原始笔记。\n")
    conflict = root / "Resource/后续传热.md"
    conflict.write_text("用户已有文件，不得覆盖。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        job["items"].sort(key=lambda i: i["path"] == "Inbox/后续传热.md")
        maintenance.make_preview(job, [], job["mocs"])
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        maintenance.run(config, db, job)
        assert job["items"][0]["state"] == "done" and job["state"] == "running"
        maintenance.run(config, db, job)
        assert job["state"] == "partial" and job["items"][1]["state"] == "failed"
        original_items = copy.deepcopy(job["items"])
        original_journal = store.get(config, "maintenance_undo", job["id"])
        repaired(config, db, job, job["items"][1]["entity"], resolutions)
        manual_map, manual_note = moc.read_bytes(), second.read_bytes()
        maintenance.action(config, db, job, "resume", None)
        maintenance.run(config, db, job)
        assert job["state"] == "completed" and job["completed"] == 2
        assert job["items"] == original_items
        assert store.get(config, "maintenance_undo", job["id"]) == original_journal
        assert moc.read_bytes() == manual_map and second.read_bytes() == manual_note
        assert conflict.read_text() == "用户已有文件，不得覆盖。\n"
        maintenance.action(config, db, job, "cancel", None)
        maintenance.run(config, db, job)
        assert job["state"] == "rollback_conflict"
        assert moc.read_bytes() == manual_map and second.read_bytes() == manual_note
