from types import SimpleNamespace

from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_evidence,
    model_review,
    semantic,
    store,
)


def test_specific_evidence_failure_is_isolated_and_recheck_keeps_other_results(
    config, ready, monkeypatch
):
    cid, vid, _, moc, note = ready
    note.write_text(
        "# 无标签资料\n固体内部的热量通过温度梯度传递。\n冷却液通过流动将热量带离高温区域。\n"
    )
    monkeypatch.setattr(model_review, "route", lambda *a: SimpleNamespace(digest="fixed"))
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    before = {p: p.read_bytes() for p in [moc, note]}
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
    assert not job["items"][0]["evaluated"]

    def reject(*args):
        raise model_evidence.EvidenceInvalid("scope_quote_missing")

    monkeypatch.setattr(model_review, "resolve", reject)
    maintenance_analysis.prepare(config, job["id"])
    isolated = store.get(config, "jobs", job["id"])
    assert isolated["state"] == "queued" and isolated["error"] is None
    assert isolated["items"][0]["analysis_detail"] == "scope_quote_missing"
    assert isolated["items"][0]["evaluated"] and not isolated["confirmed"]
    with database.connect(config.state_root) as db:
        preview = maintenance.run(config, db, isolated)
        assert preview["state"] == "preview"
        maintenance.action(config, db, preview, "recheck", None)
    resumed = store.get(config, "jobs", job["id"])
    assert resumed["error"] is None and "error_detail" not in resumed
    assert not resumed["items"][0]["evaluated"]
    assert "analysis_detail" not in resumed["items"][0]
    assert all(p.read_bytes() == raw for p, raw in before.items())
