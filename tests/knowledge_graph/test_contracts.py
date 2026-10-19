import json
from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from fiber_bundle.config import StorageError
from fiber_bundle.knowledge_graph.contracts import MAX_INPUT_BYTES, Engine, Model, decode_request
from fiber_bundle.knowledge_graph.results import decode_result, entity_id

from .support import related_result, result, span


def encoded(value):
    return json.dumps(value, ensure_ascii=True).encode()


def test_roundtrip_and_idempotency_bind_scope_versions_engine_and_not_random_task(prepared):
    request = prepared.request
    assert decode_request(request.encode()) == request
    retry = replace(request, task_id=str(uuid4()))
    assert retry.input_sha256 != request.input_sha256
    assert retry.idempotency_key == request.idempotency_key
    assert replace(request, client_id=str(uuid4())).idempotency_key != request.idempotency_key
    assert replace(request, vault_id=str(uuid4())).idempotency_key != request.idempotency_key
    model = Model("synthetic-pinned-model", "revision-1", "a" * 64, 384)
    changed = replace(request, engine=replace(request.engine, model=model))
    assert changed.configuration_sha256 != request.configuration_sha256
    assert changed.idempotency_key != request.idempotency_key
    assert replace(request, notes=request.notes[:-1]).idempotency_key != request.idempotency_key
    assert b"Bearer" not in request.encode() and b"note_path" not in request.encode()
    assert b"temp_vault" not in request.encode()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(secret="synthetic secret"),
        lambda v: v.update(schema="future-version"),
        lambda v: v["engine"].update(merge_entities=True),
        lambda v: v["engine"].update(resolve_conflicts=True),
        lambda v: v["engine"].update(unknown_relation_endpoint="create"),
        lambda v: v["engine"].update(revision="main"),
        lambda v: v["notes"][0].update(note_id="path:sample-01.md"),
        lambda v: v["notes"][0].update(note_id=[]),
        lambda v: v["notes"][0].update(title="\ud800"),
        lambda v: v["notes"][0].update(text="changed source"),
        lambda v: v["notes"][0].update(source_kind="verified_fact"),
        lambda v: v["notes"][0].update(source_kind=[]),
        lambda v: v["notes"].append(v["notes"][0]),
        lambda v: v.update(client_id="not-an-identity"),
    ],
)
def test_request_rejects_unknown_capabilities_and_unbound_sources(prepared, mutation):
    value = asdict(prepared.request)
    value = json.loads(encoded(value))
    mutation(value)
    with pytest.raises(StorageError):
        decode_request(encoded(value))


def test_pluggable_contract_and_missing_semantic_model_do_not_imply_tool_loaded(prepared):
    alternative = replace(prepared.request, engine=Engine("other_local_tool", "1", "pinned-v1"))
    assert decode_request(alternative.encode()).engine.name == "other_local_tool"
    assert prepared.request.engine.model is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":1,"schema":2}',
        b'{"score":NaN}',
        b"[]",
        b"\xff",
        b"[" * 2000,
        b"x" * (MAX_INPUT_BYTES + 1),
    ],
)
def test_bounded_strict_json(raw):
    with pytest.raises(StorageError):
        decode_request(raw)


def test_exact_quotes_candidates_and_homonyms_stay_distinct(prepared):
    request = prepared.request
    value = related_result(request)
    parsed = decode_result(encoded(value), request)
    assert parsed.review_state == "suggested"
    assert len(parsed.relations) == 1 and parsed.relations[0].score is None
    assert decode_result(encoded(result(request, with_entities=False)), request).entities == ()
    names = [note for note in request.notes if "青松" in note.text]
    first, second = span(names[0], "青松"), span(names[1], "青松")
    assert entity_id("青松", first) != entity_id("青松", second)


def test_moc_suggestion_requires_every_member_source_and_remains_candidate(prepared):
    request = prepared.request
    value = result(request)
    sources = request.notes[:2]
    evidence = [asdict(span(note, note.title)) for note in sources]
    value["mocs"] = [
        {
            "title": "合成主题建议",
            "note_ids": sorted(n.note_id for n in sources),
            "evidence": evidence,
            "method": request.engine.algorithm,
        }
    ]
    parsed = decode_result(encoded(value), request)
    assert len(parsed.mocs) == 1 and parsed.review_state == "suggested"
    value["mocs"][0]["note_ids"][0] = "note:" + str(uuid4())
    value["mocs"][0]["note_ids"].sort()
    with pytest.raises(StorageError):
        decode_result(encoded(value), request)


def test_semantic_scores_require_pinned_model_and_nonfinite_or_oversize_scores_fail(prepared):
    request = prepared.request
    value = related_result(request)
    value["relations"][0].update(score=0.7, score_kind="model_score")
    with pytest.raises(StorageError, match="semantic_model_unconfigured"):
        decode_result(encoded(value), request)
    for score in [1e999, -0.1, 10**500]:
        value["relations"][0].update(score=score, score_kind="heuristic_score")
        with pytest.raises(StorageError):
            decode_result(encoded(value), request)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(input_sha256="a" * 64),
        lambda v: v.update(configuration_sha256="b" * 64),
        lambda v: v.update(task_id=str(uuid4())),
        lambda v: v["engine"].update(version="different"),
        lambda v: v.update(review_state="accepted"),
        lambda v: v["entities"][0]["evidence"].update(quote="invented evidence"),
        lambda v: v["entities"][0]["evidence"].update(start=True),
        lambda v: v["entities"][0]["evidence"].update(line=999),
        lambda v: v["entities"][0]["evidence"].update(sha256="a" * 64),
        lambda v: v["entities"][0]["evidence"].update(note_id="note:" + str(uuid4())),
        lambda v: v["relations"][0].update(target="nonexistent-entity"),
        lambda v: v["relations"][0].update(derivation="last_resort_adjacency"),
        lambda v: v["relations"][0].update(score=0.3, score_kind="fact_probability"),
        lambda v: v["relations"][0].update(score=True, score_kind="model_score"),
        lambda v: v["relations"][0].update(score_kind=[]),
        lambda v: v["relations"][0].update(evidence=[]),
        lambda v: v["entities"].append(v["entities"][0]),
    ],
)
def test_result_rejects_fabricated_fallback_stale_or_self_accepted_output(prepared, mutation):
    value = related_result(prepared.request)
    mutation(value)
    with pytest.raises(StorageError):
        decode_result(encoded(value), prepared.request)
