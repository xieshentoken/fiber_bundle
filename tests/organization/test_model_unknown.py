import time

import pytest
from test_jobs import ready as ready
from test_model_ledger import configured

from fiber_bundle import database, model_gateway
from fiber_bundle.config import InvalidInput
from fiber_bundle.organization import (
    maintenance,
    maintenance_analysis,
    model_review,
    semantic,
    store,
)
from fiber_bundle.organization.model_unknown import UnknownCall, authorize

pytest_plugins = ["test_execution"]


def test_unknown_item_does_not_stop_remaining_analysis(config, ready, monkeypatch):
    cid, vid, root, _, note = ready
    note.write_text("# 无标签一\n需要模型语义分析的完整正文内容，原件保持。\n")
    (root / "Inbox/另一篇.md").write_text(
        "# 无标签二\n另一篇需要分析的资料正文，保留原件。"
        "这里讨论独立主题，完整内容应当继续分析，不受到前一项超时影响。\n"
    )
    monkeypatch.setattr(semantic, "recall", lambda *a: {"state": "disabled"})
    seen = []

    def resolve(*args):
        seen.append(args[2]["id"])
        if len(seen) == 1:
            raise UnknownCall(
                {"cache_key": "old", "call_id": "old-call", "reason": "model_timeout"}
            )
        return {"selected": [], "model_called": False}

    monkeypatch.setattr(model_review, "resolve", resolve)
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "additive")
    maintenance_analysis.prepare(config, job["id"])
    first = store.get(config, "jobs", job["id"])
    assert first["state"] == "queued" and not first["error"]
    assert first["items"][0]["analysis_detail"]["reason"] == "model_timeout"
    maintenance_analysis.prepare(config, job["id"])
    final = store.get(config, "jobs", job["id"])
    assert len(seen) == 2 and all(i["evaluated"] for i in final["items"])


def test_explicit_retry_preserves_original_call_and_budget(config, store, policy, monkeypatch):
    from fiber_bundle.organization import store as ledger

    selected = configured(config, policy)
    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    job = {"id": "synthetic", "vault_id": "vault"}
    with pytest.raises(UnknownCall) as caught:
        model_review.call(config, job, selected, [], "unknown", lambda: None)
    detail = caught.value.detail
    previous = ledger.get(config, "model_reviews", "unknown")
    job["client_id"] = "synthetic-client"
    job["items"] = [
        {"analysis_error": "organization_model_result_unknown", "analysis_detail": detail}
    ]
    with database.connect(config.state_root) as db:
        authorize(config, db, job)
    assert job["items"][0]["evaluated"] is False
    # Direct call fixture does not have production grant context.
    job.pop("client_id")
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *a: {
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            "choices": [{"message": {"content": "received"}}],
        },
    )
    assert model_review.call(config, job, selected, [], "unknown", lambda: None) == "received"
    saved = ledger.get(config, "model_reviews", "unknown")
    assert saved["id"] != previous["id"] and saved["attempts"][0]["id"] == previous["id"]
    with database.connect(config.state_root) as db:
        calls = db.execute("select state from model_configuration_calls").fetchall()
        reservations = db.execute("select state from model_budget_reservations").fetchall()
    assert sorted(r[0] for r in calls) == ["failed", "succeeded"]
    assert sorted(r[0] for r in reservations) == ["reserved", "settled"]


def test_retry_refuses_current_inflight_call(config, store, policy, monkeypatch):
    from fiber_bundle.organization import store as ledger

    selected = configured(config, policy)
    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(UnknownCall) as caught:
        model_review.call(
            config, {"id": "job", "vault_id": "vault"}, selected, [], "key", lambda: None
        )
    info = caught.value.detail
    job = {
        "client_id": "client",
        "vault_id": "vault",
        "items": [{"analysis_error": "organization_model_result_unknown", "analysis_detail": info}],
    }
    with database.connect(config.state_root) as db:
        db.execute(
            "update model_configuration_calls set state='running',created_at=? where id=?",
            (time.time(), info["call_id"]),
        )
        with pytest.raises(InvalidInput, match="call_inflight"):
            authorize(config, db, job)
    assert ledger.get(config, "model_reviews", "key")["state"] == "unknown"


def test_legacy_sending_cache_with_failed_call_is_reconciled_without_resend(
    config, store, policy, monkeypatch
):
    from fiber_bundle.organization import store as ledger

    selected = configured(config, policy)
    monkeypatch.setattr(model_gateway, "prepare", lambda p, v: v)
    monkeypatch.setattr(model_gateway, "complete", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    job = {"id": "old-quick", "vault_id": "vault"}
    with pytest.raises(UnknownCall):
        model_review.call(config, job, selected, [], "legacy", lambda: None)
    row = ledger.get(config, "model_reviews", "legacy")
    row["state"] = "sending"
    ledger.put(config, "model_reviews", "legacy", "vault", row)
    monkeypatch.setattr(
        model_gateway, "complete", lambda *a: pytest.fail("must not resend legacy timeout")
    )
    with pytest.raises(UnknownCall) as caught:
        model_review.call(config, job, selected, [], "legacy", lambda: None)
    assert caught.value.detail["call_id"] == row["id"]
    assert caught.value.detail["reason"] == "model_timeout"
    assert ledger.get(config, "model_reviews", "legacy")["state"] == "unknown"
