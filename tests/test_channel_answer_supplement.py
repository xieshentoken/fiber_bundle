import json

import pytest
from test_channel_natural_query import envelope, result
from test_channel_natural_query import model_channel as model_channel
from test_channel_query_recovery import interrupt_after
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import database, model_budgets, model_gateway
from fiber_bundle.channels import dispatch, query, store
from fiber_bundle.config import InvalidInput


def scenario(config, model_channel, monkeypatch, *, final=None, first_succeeds=False):
    value, root = model_channel
    lines = [
        "青松现象属于合成测试记录，观测结果保留在这段原文中。",
        "青松测试资料记录了不同边界条件，不应脱离上下文使用。",
        "青松的原因是边界变化导致输运阻碍，这段机制说明仅为隔离测试资料。",
    ]
    (root / "a.md").write_text("# 青松\n" + "\n\n".join(lines))
    calls = []

    def complete(policy, identity, payload):
        packet = json.loads(payload["messages"][1]["content"])
        calls.append((identity, packet))
        if len(calls) == 1:
            return envelope({"queries": ["青松"]})
        if len(calls) == 2 and not first_succeeds:
            assert len(packet["quotations"]) == 2
            return envelope({"claims": []})
        if final:
            return final(packet, root)
        return envelope(
            {"claims": [{"text": "这份合成资料保留了适用条件。", "citations": ["[1]"]}]}
        )

    monkeypatch.setattr(model_gateway, "complete", complete)
    assert dispatch.capture(
        config, value["id"], value["revision"], "supplement", "ou_synthetic", "介绍青松"
    )
    return value, root, calls


def test_abstention_adds_exact_passage_and_uses_one_distinct_budgeted_call(
    config, model_channel, monkeypatch
):
    value, root, calls = scenario(config, model_channel, monkeypatch)
    before = (root / "a.md").read_bytes()
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    packet = data["result"]
    assert state == "ready" and packet["status"] == "unverified_draft"
    assert len(calls) == len({identity for identity, _ in calls}) == 3
    assert len(calls[2][1]["quotations"]) == 3
    assert packet["evidence_recovery"] == {"status": "supplemented", "rounds": 1}
    assert packet["citation_integrity"] == "verified"
    assert packet["semantic_sufficiency"] == "not_assessed"
    assert all(s["snippet"] in before.decode() for s in packet["sources"])
    assert (root / "a.md").read_bytes() == before
    with database.connect(config.state_root) as db, store.readonly(config) as side:
        query.recheck(config, db, store.get(side, value["id"]), packet)
        assert model_budgets.inspect(db)[0]["used"] == 450
        assert db.execute("SELECT count(*) FROM model_budget_reservations").fetchone()[0] == 3
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 3


def test_successful_first_answer_does_not_supplement(config, model_channel, monkeypatch):
    value, _, calls = scenario(config, model_channel, monkeypatch, first_succeeds=True)
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 2 and result(config, value)[1]["result"]["claims"]


def test_no_new_evidence_does_not_make_another_model_call(config, model_channel, monkeypatch):
    value, root, calls = scenario(config, model_channel, monkeypatch)
    (root / "a.md").write_text(
        "# 青松\n青松只有第一段合成资料，结论尚未验证。\n\n青松只有第二段合成资料，原因尚不清楚。"
    )
    dispatch.advance(config, value["id"], value["revision"])
    packet = result(config, value)[1]["result"]
    assert len(calls) == 2 and packet["status"] == "insufficient_evidence"
    assert packet["evidence_recovery"]["status"] == "no_new_evidence"


def test_second_abstention_is_final_and_retains_all_evidence(config, model_channel, monkeypatch):
    value, _, calls = scenario(
        config, model_channel, monkeypatch, final=lambda *_: envelope({"claims": []})
    )
    dispatch.advance(config, value["id"], value["revision"])
    packet = result(config, value)[1]["result"]
    assert len(calls) == 3 and packet["status"] == "insufficient_evidence"
    assert len(packet["sources"]) == 3 and packet["claims"] == []


@pytest.mark.parametrize("platform", ["feishu", "weixin"])
def test_supplement_interrupt_resumes_cached_request_without_repeating_paid_calls(
    config, model_channel, monkeypatch, platform
):
    value, _, calls = scenario(config, model_channel, monkeypatch)
    with store.opened(config) as db:
        current = store.get(db, value["id"])
        current["value"]["platform"] = platform
        db.execute(
            "UPDATE channels SET value=? WHERE id=?", (json.dumps(current["value"]), value["id"])
        )
    interrupt_after(monkeypatch, "answer_supplement")
    with pytest.raises(database.LockTimeout):
        dispatch.advance(config, value["id"], value["revision"])
    saved = result(config, value)[1]["query_processing"]
    assert (
        saved["phase"] == "answer_supplement" and saved["source"]["retrieval_recipe"]["supplement"]
    )
    dispatch.advance(config, value["id"], value["revision"])
    assert (
        len(calls) == 3 and result(config, value)[1]["result"]["citation_integrity"] == "verified"
    )
    with database.connect(config.state_root) as db:
        assert model_budgets.inspect(db)[0]["used"] == 450


@pytest.mark.parametrize("failure", ["quote", "citation", "changed", "timeout"])
def test_supplement_keeps_quote_source_and_unknown_result_guards(
    config, model_channel, monkeypatch, failure
):
    def final(packet, root):
        if failure == "changed":
            (root / "a.md").write_text("# 青松\n青松来源已被修改，不能发送此前冻结证据。")
        if failure == "timeout":
            raise InvalidInput("model_timeout")
        payload = {
            "claims": [
                {"text": "合成结论。", "citations": ["[99]" if failure == "citation" else "[1]"]}
            ]
        }
        if failure == "quote":
            payload["quotations"] = [{"citation": "[1]", "quote": "伪造原文"}]
        return envelope(payload)

    value, _, calls = scenario(config, model_channel, monkeypatch, final=final)
    dispatch.advance(config, value["id"], value["revision"])
    packet = result(config, value)[1]
    assert packet["result"]["status"] == "query_failed"
    expected = {
        "quote": "answer_selection_quote_mismatch",
        "citation": "answer_draft_rejected_unknown_citation",
        "changed": "channel_source_changed",
        "timeout": "model_timeout",
    }
    assert packet["error_code"] == expected[failure]
    dispatch.advance(config, value["id"], value["revision"])
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["budget", "grant", "cache"])
def test_supplement_admission_and_resume_fail_closed(config, model_channel, monkeypatch, failure):
    from fiber_bundle import auth
    from fiber_bundle.channels import model_call

    value, _, calls = scenario(config, model_channel, monkeypatch)
    original = model_call.complete

    def complete(*args):
        if args[3] == "answer_supplement":
            with database.connect(config.state_root) as db:
                if failure == "budget":
                    # Exhaust the existing shared policy, never create a new one.
                    row = db.execute(
                        "SELECT credential_id,value FROM model_budget_policies"
                    ).fetchone()
                    policy = json.loads(row[1])
                    policy["limit"] = 300
                    db.execute(
                        "UPDATE model_budget_policies SET value=? WHERE credential_id=?",
                        (json.dumps(policy), row[0]),
                    )
                elif failure == "grant":
                    auth.set_grant(
                        db,
                        value["settings"]["client_id"],
                        value["settings"]["vault_id"],
                        query=False,
                        original=False,
                        add=False,
                    )
        return original(*args)

    if failure == "cache":
        interrupt_after(monkeypatch, "answer_supplement")
        with pytest.raises(database.LockTimeout):
            dispatch.advance(config, value["id"], value["revision"])
        # Lose only the third response; the unknown paid result must not be retried.
        (config.state_root / "channel-query-results" / (calls[-1][0] + ".json")).unlink()
    else:
        monkeypatch.setattr(model_call, "complete", complete)
    if failure == "grant":
        from fiber_bundle.channels.validation import ChannelValidationError

        with pytest.raises(ChannelValidationError) as denied:
            dispatch.advance(config, value["id"], value["revision"])
        assert denied.value.status == 403
        assert result(config, value)[0] == "processing" and len(calls) == 2
        return
    dispatch.advance(config, value["id"], value["revision"])
    data = result(config, value)[1]
    assert data["result"]["status"] == "query_failed"
    if failure == "budget":
        assert data["error_code"] == "model_key_budget_exhausted"
    if failure == "cache":
        assert data["error_code"] == "channel_model_result_unknown"
    assert len(calls) == (3 if failure == "cache" else 2)
