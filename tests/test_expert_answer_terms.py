"""Only registered expert hits reuse bounded, already prepared local search terms."""

from fiber_bundle import expert_answers


def test_conversational_expert_query_uses_existing_terms_without_model(monkeypatch):
    calls = []

    def query(config, db, cid, module, version, question, limit):
        calls.append((module, version, question))
        found = question == "thermal conductivity"
        return {
            "module_id": module,
            "version": version,
            "query_result": {
                "matched_object_ids": ["conductivity"] if found else [],
                "figures": [{"id": "figure-1", "image_sha256": "a" * 64}] if found else [],
                "results": [],
                "behavior": "reference" if found else "abstain",
            },
        }

    monkeypatch.setattr(expert_answers.expert_query, "query", query)
    source = {
        "expert_projection": True,
        "expert_module_id": "expert:known",
        "expert_module_version": "version-1",
        "citation": "[1]",
        "snippet": "source text",
    }
    response = {"sources": [source], "retrieval_queries": ["thermal conductivity"]}
    result = expert_answers.enrich(None, None, "client", "请解释导热系数。测试标记A", response)
    assert calls == [
        ("expert:known", "version-1", "请解释导热系数。测试标记A"),
        ("expert:known", "version-1", "thermal conductivity"),
    ]
    assert result["expert_blocks"][0]["media"][0]["resource_id"] == "figure-1"
    assert result["expert_blocks"][0]["object_ids"] == ["conductivity"]


def test_ordinary_notes_do_not_enter_expert_lookup(monkeypatch):
    def forbidden(*args):
        raise AssertionError("ordinary note routed to expert media")

    monkeypatch.setattr(expert_answers.expert_query, "query", forbidden)
    response = {"sources": [{"note_path": "00-Inbox/ordinary.md"}], "retrieval_queries": ["topic"]}
    assert expert_answers.enrich(None, None, "client", "question", response) is response


def test_package_fallback_is_bounded_and_skips_invalid_terms(monkeypatch):
    calls = []

    def query(config, db, cid, module, version, question, limit):
        calls.append(question)
        return {"module_id": module, "version": version, "query_result": {}}

    monkeypatch.setattr(expert_answers.expert_query, "query", query)
    response = {
        "sources": [
            {
                "expert_projection": True,
                "expert_module_id": "expert:known",
                "expert_module_version": "v1",
                "citation": "[1]",
                "snippet": "text",
            }
        ],
        "retrieval_queries": [None, "", "x" * 201, "a", "b", "c", "d"],
    }
    expert_answers.enrich(None, None, "client", "original", response)
    assert calls == ["original", "a", "b", "c"]
