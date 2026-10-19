from unittest.mock import Mock

from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_review,
    semantic,
    store,
)


def test_quick_batch_never_calls_model_or_embedding_and_reuses_cache(config, ready, monkeypatch):
    cid, vid, root, moc, note = ready
    note.write_text("# 无标签正文\n正文讨论固体传热和导热，但不凭标题强行分类。\n")
    for i in range(12):
        (root / f"Inbox/普通{i}.md").write_text("# 普通资料\n无明确标签或分类规则。\n")
    model = Mock(side_effect=AssertionError("quick mode must not invoke a model"))
    embedding = Mock(side_effect=AssertionError("quick mode must not embed"))
    monkeypatch.setattr(model_review, "resolve", model)
    monkeypatch.setattr(semantic, "recall", embedding)
    before = {p: p.read_bytes() for p in [note, moc]}
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
    maintenance_analysis.prepare(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert all(i["evaluated"] for i in result["items"])
    assert all(i["analysis_origin"] == "local" for i in result["items"])
    assert not any(i["selected"] for i in result["items"])
    with database.connect(config.state_root) as db:
        result = maintenance.run(config, db, result)
        assert result["state"] == "preview"
        maintenance.action(config, db, result, "cancel", None)
        again = maintenance.submit(config, db, cid, vid, "quick")
    maintenance_analysis.prepare(config, again["id"])
    assert all(
        i["analysis_origin"] == "cache" for i in store.get(config, "jobs", again["id"])["items"]
    )
    model.assert_not_called()
    embedding.assert_not_called()
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_quick_supplement_is_explicit_and_keeps_completed_results(config, ready, monkeypatch):
    cid, vid, root, _, _ = ready
    note = root / "Inbox/未分类.md"
    note.write_text("# 无标签资料\n这里是尚未找到可靠分类的正文内容，保持原样。\n")
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    calls = []
    monkeypatch.setattr(
        model_review,
        "resolve",
        lambda *a: calls.append(a) or {"selected": [], "model_called": True, "model_used": True},
    )
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
    maintenance_analysis.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        job = maintenance.run(config, db, store.get(config, "jobs", job["id"]))
        first = next(i.copy() for i in job["items"] if i["selected"])
        maintenance.action(config, db, job, "supplement", None)
    assert not calls
    maintenance_analysis.prepare(config, job["id"])
    assert len(calls) == 1
    assert (
        next(
            i
            for i in store.get(config, "jobs", job["id"])["items"]
            if i["entity"] == first["entity"]
        )
        == first
    )


def test_real_worker_scheduling_advances_local_batch_to_preview(config, ready, monkeypatch):
    from fiber_bundle.organization import jobs

    cid, vid, _, _, note = ready
    note.write_text("# 待分类正文\n没有明确规则的内容，应快速给出预览而不一直等待。\n")
    monkeypatch.setattr(
        model_review, "resolve", lambda *a: (_ for _ in ()).throw(AssertionError("no model"))
    )
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
    jobs.tick(config)
    assert all(i["evaluated"] for i in store.get(config, "jobs", job["id"])["items"])
    jobs.tick(config)
    assert store.get(config, "jobs", job["id"])["state"] == "preview"
