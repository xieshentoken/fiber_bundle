import copy
import json

import pytest

from fiber_bundle.agent_connections import turn
from fiber_bundle.agent_connections.turn import TurnBlocked

from .support import Harness, envelope, passage, proposal


def blocked(harness, code, **callbacks):
    with pytest.raises(TurnBlocked, match="^agent_" + code + "$"):
        harness.run(**callbacks)


def test_success_is_retrieved_and_verified_before_one_generation():
    h = Harness()
    result = h.run()
    assert h.events == ["retrieve", "verify", "generate", "verify"]
    assert result["state"] == "draft" and result["semantic_verified"] is False
    assert result["sources"][0]["sha256"] == passage()["sha256"]
    assert result["sources"][0]["source_label"] == "历史笔记，来源未核实"
    assert result["connection_revision"] == 1
    assert result["configuration_sha256"] == h.context["configuration_sha256"]


@pytest.mark.parametrize(
    "case,code",
    [
        ("pause", "paused"),
        ("scope", "scope_denied"),
        ("limit", "input_limit"),
        ("cancel", "cancelled"),
    ],
)
def test_initial_block_never_retrieves_or_generates(case, code):
    h = Harness()
    if case == "pause":
        h.context["enabled"] = False
    elif case == "scope":
        h.request.vault_ids = ["supplier"]
    elif case == "limit":
        h.configure(limits={"queries": 1})
        h.request.retrieval_queries.append("target")
    else:
        h.stopped = True
    blocked(h, code)
    assert h.events == []


def test_retrieval_failure_is_not_empty_success_or_retried():
    h = Harness()

    def failed(*args):
        h.events.append("failed")
        raise RuntimeError("private exception, synthetic token must not escape")

    blocked(h, "retrieval_failed", retrieve=failed)
    assert h.events == ["failed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", 2),
        ("enabled", False),
        ("client_id", "other"),
        ("credential_generation", 2),
        ("authorization_sha256", "b" * 64),
    ],
)
@pytest.mark.parametrize("phase", ["retrieve", "generate"])
def test_identity_or_authorization_change_invalidates_the_turn(field, value, phase):
    h = Harness()
    original = getattr(h, phase)

    def changed(*args):
        result = original(*args)
        h.context[field] = value
        return result

    blocked(h, "context_changed", **{phase: changed})
    assert h.events.count("generate") == (phase == "generate")


@pytest.mark.parametrize("phase", ["retrieve", "generate"])
def test_cancellation_discards_pending_or_generated_results(phase):
    h = Harness()
    original = getattr(h, phase)

    def cancelled(*args):
        result = original(*args)
        h.stopped = True
        return result

    blocked(h, "cancelled", **{phase: cancelled})
    assert h.events.count("generate") == (phase == "generate")


def test_timeout_after_retrieval_never_generates(monkeypatch):
    h, now = Harness(), [0]
    monkeypatch.setattr(turn.time, "monotonic", lambda: now[0])

    def delayed(*args):
        result = h.retrieve(*args)
        now[0] = 61
        return result

    blocked(h, "timeout", retrieve=delayed)
    assert h.events == ["retrieve"]


@pytest.mark.parametrize("phase", [1, 2])
def test_changed_source_prevents_generation_or_final_delivery(phase):
    h = Harness()

    def verify(prepared):
        h.verify(prepared)
        if h.events.count("verify") == phase:
            raise ValueError("source or grant no longer matches")

    blocked(h, "evidence_changed", verify_evidence=verify)
    assert h.events.count("generate") == phase - 1


def test_exact_deduplication_preserves_original_query_citation_mapping():
    h = Harness()
    h.request.retrieval_queries.append("target")
    result = h.run()
    assert len(result["sources"]) == 1
    assert result["retrieval_queries"] == ["coolant", "target"]
    assert result["sources"][0]["retrieval_matches"] == [
        {"query_index": 1, "citation": "[1]"},
        {"query_index": 2, "citation": "[1]"},
    ]


def test_different_snapshot_of_same_source_in_two_queries_fails():
    h = Harness()
    h.request.retrieval_queries.append("target")

    def changed(*args):
        response = h.retrieve(*args)
        if h.events.count("retrieve") == 2:
            response["results"][0]["sha256"] = "b" * 64
        return response

    blocked(h, "evidence_invalid", retrieve=changed)
    assert "generate" not in h.events


@pytest.mark.parametrize(
    "case",
    [
        "status",
        "scope",
        "foreign",
        "path",
        "lines",
        "hash",
        "count",
        "citation",
        "bytes",
        "nan",
        "provenance",
    ],
)
def test_malformed_or_out_of_scope_evidence_blocks_generation(case):
    h = Harness()
    if case == "status":
        h.response["status"] = "no_evidence"
    elif case == "scope":
        h.response["scope"] = "session"
    elif case == "count":
        h.response["results"] *= 6
    elif case == "bytes":
        h.response["arbitrary"] = "x" * 262144
    elif case == "nan":
        h.response["arbitrary"] = float("nan")
    else:
        key, value = {
            "foreign": ("vault_id", "supplier"),
            "path": ("note_path", "../secret.md"),
            "lines": ("line_end", 3),
            "hash": ("sha256", "invalid"),
            "citation": ("citation", "[9]"),
            "provenance": ("source_kind", "verified_fact"),
        }[case]
        h.response["results"][0][key] = value
    blocked(h, "evidence_invalid")
    assert h.events == ["retrieve"]


def test_evidence_and_prompt_limits_fail_without_truncation():
    h = Harness()
    h.configure(limits={"evidence_chars": 400})
    h.response = envelope([passage(snippet="x" * 401)])
    blocked(h, "evidence_invalid")
    h = Harness()
    h.configure(limits={"prompt_bytes": 4096})
    h.response = envelope([passage(snippet="汉" * 2000)])
    blocked(h, "input_limit")
    assert h.events == ["retrieve"]


def test_expert_draft_and_document_provenance_survive_without_original_downloads():
    h = Harness()
    h.response = envelope(
        [
            passage(
                source_kind="agent_analysis",
                independent_evidence=False,
                artifact_stage="draft",
                review_state="deferred",
                knowledge_verified=False,
                source_label="完整 Skill 候选分析，独立语义审核待完成",
                document_part={"role": "extraction", "matches_reviewed_extraction": True},
                originals=[{"url": "/private-download"}],
                ignored_secret="not-to-forward",
            )
        ]
    )
    source = h.run()["sources"][0]
    assert source["artifact_stage"] == "draft" and source["review_state"] == "deferred"
    assert source["knowledge_verified"] is False and source["independent_evidence"] is False
    assert source["document_part"]["role"] == "extraction"
    assert "originals" not in source and "ignored_secret" not in source


@pytest.mark.parametrize(
    "changes",
    [
        {"source_kind": "agent_analysis", "independent_evidence": True},
        {"source_kind": "agent_analysis", "knowledge_verified": True},
        {"artifact_stage": "draft", "knowledge_verified": True},
    ],
)
def test_analysis_or_draft_cannot_be_upgraded_to_verified_evidence(changes):
    h = Harness()
    h.response = envelope([passage(**changes)])
    blocked(h, "evidence_invalid")
    assert h.events == ["retrieve"]


@pytest.mark.parametrize(
    "policy,supplement",
    [("evidence_only", None), ("augmented", "Model knowledge, not a source fact.")],
)
def test_no_evidence_is_explicit_and_policy_controls_supplement(policy, supplement):
    h = Harness()
    h.configure(answer_policy=policy)
    h.response, h.raw = envelope([]), proposal([], supplement)
    result = h.run()
    assert result["retrieval_status"] == "no_evidence" and result["sources"] == []
    assert result["claims"] == [] and result["model_supplement"] == supplement


@pytest.mark.parametrize(
    "raw",
    [
        proposal([{"text": "Made up", "citations": ["[2]"]}]),
        proposal([{"text": "Inline [1]", "citations": ["[1]"]}]),
        proposal([{"text": "Duplicate", "citations": ["[1]", "[1]"]}]),
        proposal([], "Model text [1]"),
        "```json\n{}\n```",
        '{"schema_version":"fiber-agent-answer/v1","claims":[],"claims":[],"model_supplement":null}',
        '{"schema_version":"fiber-agent-answer/v1","claims":[],"model_supplement":NaN}',
        proposal()[:-1] + ',"sources":[]}',
        "x" * 32769,
    ],
)
def test_malformed_answers_and_fabricated_citations_are_not_delivered(raw):
    h = Harness()
    h.raw = raw
    blocked(h, "answer_invalid")
    assert h.events.count("generate") == 1


def test_no_hit_cannot_be_used_for_cited_claims_and_evidence_only_has_no_supplement():
    h = Harness()
    h.response = envelope([])
    blocked(h, "answer_invalid")
    h = Harness()
    h.configure(answer_policy="evidence_only")
    h.raw = proposal(supplement="General knowledge")
    blocked(h, "answer_invalid")


def test_failed_generation_is_not_retried_and_error_is_redacted():
    h = Harness()

    def generate(prepared):
        h.events.append("generate")
        raise RuntimeError("synthetic private provider error")

    blocked(h, "generation_failed", generate=generate)
    assert h.events == ["retrieve", "verify", "generate"]


def test_prepared_snapshot_is_immutable_and_cannot_replace_host_sources():
    h = Harness()

    def generate(prepared):
        assert "coolant" not in repr(prepared)
        detached = json.loads(prepared.evidence_json)
        detached[0]["snippet"] = "altered"
        return h.generate(prepared)

    expected = copy.deepcopy(h.response)
    result = h.run(generate=generate)
    assert result["sources"][0]["snippet"] == expected["results"][0]["snippet"]
