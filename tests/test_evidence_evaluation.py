"""Fixed synthetic M5 retrieval set; exact hit identities, source fidelity and scope."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_api import add_client, permissions, vault_ids
from test_session_search import intake

from fiber_bundle import model_gateway

FIXTURE = json.loads((Path(__file__).parent / "fixtures/m5_evidence_eval.json").read_text())


@pytest.fixture
def corpus(http, config):
    cid, headers = add_client(http, "evaluation-owner")
    ids = vault_ids(http)
    permissions(http, cid, ids["personal"], query=True, add=True)
    roots = {name: config.project_root / path for name, path in config.vaults}
    paths = []
    for note in FIXTURE["notes"]:
        path = roots[note["vault"]] / note["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note["content"])
        paths.append(path)
    owner = cid, headers, ids
    intake(http, owner, body="当前会话型号 SESSION-42 压力 42 psi。".encode())
    intake(
        http, owner, session="past", event="past", body="旧会话 ONLY-PAST 压力 77 psi。".encode()
    )
    other, other_headers = add_client(http, "evaluation-other")
    permissions(http, other, ids["personal"], query=True, add=True)
    intake(
        http,
        (other, other_headers, ids),
        event="other",
        body="其他客户端 ONLY-OTHER 压力 88 psi。".encode(),
    )
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}
    yield headers, ids, roots
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_fixed_evidence_evaluation(http, corpus, case):
    headers, ids, roots = corpus
    if case.get("default_query"):
        assert (
            http.patch("/admin/vaults/" + ids["personal"], json={"default_query": True}).status_code
            == 200
        )
    request = {
        "question": "这些资料提供了什么证据？",
        "retrieval_query": case["query"],
        "limit": case.get("limit", 10),
    }
    if case["scope"] is not None:
        request["vault_ids"] = [ids[name] for name in case["scope"]]
    if case.get("session"):
        request["session_id"] = case["session"]
    with patch.object(
        model_gateway, "complete", side_effect=AssertionError("evaluation must stay local")
    ):
        response = http.post("/v1/answers", headers=headers, json=request)
        repeat = http.post("/v1/answers", headers=headers, json=request)
    assert response.status_code == repeat.status_code == case["http_status"]
    assert response.json() == repeat.json()  # Fresh snapshot rebuild is deterministic.
    assert response.headers["cache-control"] == "no-store"
    if case["http_status"] != 200:
        assert "SECRET-SUPPLIER" not in response.text and "quotations" not in response.json()
        return
    data = response.json()
    identities = {
        ("note:" + s["note_path"]) if s["note_path"] else ("session:" + s["session_id"])
        for s in data["sources"]
    }
    assert identities == set(case["expected"])  # No missing or extraneous evidence hits.
    assert len(data["sources"]) == len(case["expected"])
    assert data["status"] == ("evidence_only" if case["expected"] else "insufficient_evidence")
    assert data["model_used"] is False
    assert data["semantic_sufficiency"] == data["conflict_assessment"] == "not_assessed"
    assert len(data["comparisons"]) == case["comparisons"]
    assert all(c["status"] == "needs_context_review" for c in data["comparisons"])
    assert any("时效" in text for text in data["cautions"])
    for index, (quote, source) in enumerate(
        zip(data["quotations"], data["sources"], strict=True), 1
    ):
        assert quote == {"citation": f"[{index}]", "quote": source["snippet"]}
        assert source["citation"] == quote["citation"] and source["originals"] == []
        if source["note_path"]:
            path = roots["personal"] / source["note_path"]
            assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
            assert source["snippet"] in path.read_text().splitlines()[source["line"] - 1]
            assert source["source_kind"] == "historical_note"
        else:
            assert source["source_kind"] == "session_material"
            assert source["snippet"] == "当前会话型号 SESSION-42 压力 42 psi。"


def test_equal_heading_and_body_scores_choose_original_body():
    from fiber_bundle.search_matching import best_passage, terms

    result = best_passage("泵A实验", "# 泵A实验\n泵A 压力 100 psi。\n", terms("泵A"))
    assert result is not None and result[1:] == (2, "泵A 压力 100 psi。")


def test_title_only_hit_still_returns_heading():
    from fiber_bundle.search_matching import best_passage, terms

    result = best_passage("专题介绍", "# 专题介绍\n内容没有查询词。\n", terms("专题"))
    assert result is not None and result[1:] == (1, "# 专题介绍")
