import copy
import json
from types import SimpleNamespace

import pytest
from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_review,
    semantic,
    store,
)


def legacy(config, ready, monkeypatch):
    cid, vid, root, moc, note = ready
    moc.write_text("---\ntype: moc\n---\n# 热管理 MOC\n- 温度梯度与流动冷却。\n")
    note.write_text(
        "# 无标签资料\n**固体内部的热量通过温度梯度传递。**\n冷却液通过流动将热量带离高温区域。\n"
    )
    (root / "Inbox/图片资料.md").write_text("# 图片资料\n![[image.png]]\n")
    monkeypatch.setattr(
        model_review, "route", lambda *a: SimpleNamespace(digest="fixed", max_input_tokens=16384)
    )
    monkeypatch.setattr(semantic, "recall", lambda *a, **kw: {"state": "disabled"})
    with database.connect(config.state_root) as db:
        parent = maintenance.submit(config, db, cid, vid, "quick")
        entities, mocs = maintenance.validate(config, db, parent)
    old = next(i for i in parent["items"] if i["path"].endswith("传热.md"))
    entity = next(e for e in entities if e["id"] == old["entity"])

    def capture(config, job, selected, messages, key, check):
        raw = json.dumps(
            {
                "moc_id": mocs[0]["id"],
                "evidence": [
                    "固体内部的热量通过温度梯度传递。",
                    "冷却液通过流动将热量带离高温区域。",
                    "模型额外改写的这条引文。",
                ],
                "scope_quote": "温度梯度与流动冷却。",
                "reason": "主题匹配",
            }
        )
        store.put(
            config,
            "model_reviews",
            key,
            vid,
            {
                "id": "old-call",
                "state": "succeeded",
                "reply": raw,
                "job": parent["id"],
                "usage": {"total_tokens": 12},
            },
        )
        return raw

    monkeypatch.setattr(model_review, "call", capture)
    model_review.resolve(config, parent, entity, mocs, old, lambda: None)
    old.update(
        selected=[],
        status="review",
        analysis_error="organization_model_evidence_invalid",
        evaluated=True,
        state="done",
    )
    for i in parent["items"]:
        i.update(evaluated=True, state="done")
    parent.update(state="completed", completed=len(parent["items"]), confirmed=True)
    store.put(config, "jobs", parent["id"], vid, parent)
    monkeypatch.setattr(
        model_review, "call", lambda *a: pytest.fail("cache recovery must never send")
    )
    return parent, old, note


def follow(config, parent):
    with database.connect(config.state_root) as db:
        return maintenance.action(config, db, parent, "recover_cache", None)


def test_recovery_reuses_reply_preserves_original_and_requires_new_confirmation(
    config, ready, monkeypatch
):
    parent, old, note = legacy(config, ready, monkeypatch)
    before = copy.deepcopy(parent)
    rows = store.rows(config, "model_reviews")
    raw = note.read_bytes()
    child = follow(config, parent)
    assert child["id"] != parent["id"] and not child["confirmed"]
    assert all(not i["selected"] and not i["target"] for i in child["items"])
    assert follow(config, parent)["id"] == child["id"]
    for _ in child["items"]:
        maintenance_analysis.prepare(config, child["id"])
    with database.connect(config.state_root) as db:
        child = maintenance.run(config, db, store.get(config, "jobs", child["id"]))
        assert child["state"] == "preview"
        assert next(i for i in child["items"] if i["entity"] == old["entity"])["selected"]
        assert (
            next(i for i in child["items"] if i["title"] == "图片资料")["review_reason"]
            == "body_text_required"
        )
        for action in ("supplement",):
            with pytest.raises(InvalidInput, match="cache_only"):
                maintenance.action(config, db, child, action, None)
    assert store.rows(config, "model_reviews") == rows
    assert store.get(config, "jobs", parent["id"]) == before
    assert note.read_bytes() == raw


@pytest.mark.parametrize("change", ["source", "moc", "policy"])
def test_changed_bindings_cannot_reuse_old_evidence(config, ready, monkeypatch, change):
    parent, _, note = legacy(config, ready, monkeypatch)
    if change == "source":
        note.write_text(note.read_text() + "修改过的资料。\n")
    elif change == "moc":
        ready[3].write_text(ready[3].read_text() + "其他实质定义。\n")
    else:
        monkeypatch.setattr(model_review, "route", lambda *a: SimpleNamespace(digest="changed"))
    with pytest.raises(IntegrityError):
        follow(config, parent)


@pytest.mark.parametrize("kind", ["unknown", "missing"])
def test_unknown_and_missing_replies_remain_isolated_without_sends(
    config, ready, monkeypatch, kind
):
    parent, old, _ = legacy(config, ready, monkeypatch)
    if kind == "unknown":
        old.update(
            analysis_error="organization_model_result_unknown",
            analysis_detail={"call_id": "old-call", "reason": "model_timeout"},
        )
        store.put(config, "jobs", parent["id"], parent["vault_id"], parent)
    else:
        with store.connection(config, write=True) as db:
            assert db is not None
            db.execute("DELETE FROM model_reviews")
    child = follow(config, parent)
    for _ in child["items"]:
        maintenance_analysis.prepare(config, child["id"])
    value = store.get(config, "jobs", child["id"])
    item = next(i for i in value["items"] if i["entity"] == old["entity"])
    assert item["analysis_error"] == (
        "organization_model_result_unknown"
        if kind == "unknown"
        else "organization_model_cache_incomplete"
    )
    assert value["state"] == "queued" and not value["error"]


def test_pause_resume_and_cancel_keep_cached_work_and_allow_new_followup(
    config, ready, monkeypatch
):
    parent, _, note = legacy(config, ready, monkeypatch)
    child = follow(config, parent)
    maintenance_analysis.prepare(config, child["id"])
    first = store.get(config, "jobs", child["id"])
    saved = [copy.deepcopy(i) for i in first["items"] if i["evaluated"]]
    with database.connect(config.state_root) as db:
        maintenance.action(config, db, first, "pause", None)
        maintenance.action(config, db, first, "resume", None)
        assert all(i in first["items"] for i in saved)
        maintenance.action(config, db, first, "cancel", None)
    assert follow(config, parent)["id"] != child["id"]
    assert note.exists()


def test_missing_supplement_only_requeues_missing_items_and_is_not_automatic(
    config, ready, monkeypatch
):
    parent, old, _ = legacy(config, ready, monkeypatch)
    child = follow(config, parent)
    for _ in child["items"]:
        maintenance_analysis.prepare(config, child["id"])
    with database.connect(config.state_root) as db:
        child = maintenance.run(config, db, store.get(config, "jobs", child["id"]))
        item = next(i for i in child["items"] if i["entity"] == old["entity"])
        item.update(
            analysis_error="organization_model_cache_incomplete",
            analysis_detail={"completed_windows": 1, "total_windows": 2},
        )
        image = copy.deepcopy(next(i for i in child["items"] if i["title"] == "图片资料"))
        store.put(config, "jobs", child["id"], child["vault_id"], child)
        maintenance.action(config, db, child, "supplement_missing", None)
        assert child["cache_recovery"]["allow_missing"]
        assert not item["evaluated"] and "analysis_error" not in item
        assert image in child["items"]
    assert store.get(config, "jobs", parent["id"]) == parent


def test_unknown_retry_is_separately_authorized_and_does_not_enable_other_resends(
    config, ready, monkeypatch
):
    from fiber_bundle.organization import model_unknown

    parent, old, _ = legacy(config, ready, monkeypatch)
    old.update(
        analysis_error="organization_model_result_unknown",
        analysis_detail={"call_id": "old-call", "reason": "model_timeout"},
    )
    store.put(config, "jobs", parent["id"], parent["vault_id"], parent)
    child = follow(config, parent)
    for _ in child["items"]:
        maintenance_analysis.prepare(config, child["id"])
    with database.connect(config.state_root) as db:
        child = maintenance.run(config, db, store.get(config, "jobs", child["id"]))

        def authorize(config, db, job):
            unknown = [
                i
                for i in job["items"]
                if i.get("analysis_error") == "organization_model_result_unknown"
            ]
            assert len(unknown) == 1
            unknown[0].update(evaluated=False)
            unknown[0].pop("analysis_error")

        monkeypatch.setattr(model_unknown, "authorize", authorize)
        maintenance.action(config, db, child, "retry_unknown", None)
    assert child["cache_recovery"]["retry_unknown"] == [old["entity"]]
    assert not child["cache_recovery"].get("allow_missing")
    received = []

    def resolve(*a, **kw):
        received.append(kw["cached_only"])
        return {"selected": [], "model_called": True}

    monkeypatch.setattr(model_review, "resolve", resolve)
    maintenance_analysis.prepare(config, child["id"])
    assert received == [False]
    assert store.get(config, "jobs", parent["id"]) == parent


def test_next_round_after_application_excludes_applied_notes(config, ready, monkeypatch):
    from test_maintenance_apply import execute

    from fiber_bundle import moc_store

    parent, old, note = legacy(config, ready, monkeypatch)
    child = follow(config, parent)
    for _ in child["items"]:
        maintenance_analysis.prepare(config, child["id"])
    with database.connect(config.state_root) as db:
        child = maintenance.run(config, db, store.get(config, "jobs", child["id"]))
        completed = execute(config, db, child)
    assert completed["state"] == "completed"
    assert not note.exists()
    next_job = follow(config, completed)
    assert next_job["id"] != completed["id"]
    assert all(i["entity"] != old["entity"] for i in next_job["items"])
    assert follow(config, parent)["id"] == next_job["id"]
    assert len(moc_store.load(config, parent["vault_id"], child["mocs"][0]["id"]).members) == 1
