import math

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.knowledge_graph.evaluation import eligible, hybrid_candidates, technical_ids


def test_exact_technical_ids_allow_adjacent_chinese_but_reject_prefixes():
    assert technical_ids("材料AB-123的高温特性") == ("AB-123",)
    assert eligible("MC-CP", "英文简称为MC-CP。")
    assert eligible("材料AB-12的", "材料型号AB-12。")
    assert not eligible("AB-123", "材料型号AB-12。")
    assert not eligible("AB-12", "材料型号AB-123。")


def test_candidate_preserves_actual_literal_ranking(prepared):
    ids = [n.note_id for n in prepared.request.notes]
    assert hybrid_candidates(prepared.request, "资料", ids[:10], [1.0] * len(ids)) == ids[:10]
    assert hybrid_candidates(prepared.request, "资料", ids[:2], [0.1] * len(ids)) == ids[:2]


def test_full_literal_page_cannot_overflow_when_semantic_first_hit_is_new(prepared):
    ids = [n.note_id for n in prepared.request.notes]
    scores = [0.1] * len(ids)
    scores[-1] = 0.99
    assert hybrid_candidates(prepared.request, "资料", ids[:10], scores) == ids[:10]


def test_candidate_does_not_invent_a_missing_identifier(prepared):
    assert (
        hybrid_candidates(prepared.request, "AB-123", [], [1.0] * len(prepared.request.notes)) == []
    )


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -1.1, "0.9"])
def test_candidate_rejects_invalid_worker_scores(prepared, value):
    with pytest.raises(InvalidInput, match="score_invalid"):
        hybrid_candidates(prepared.request, "资料", [], [value] * len(prepared.request.notes))


def test_candidate_rejects_unselected_baseline_source(prepared):
    with pytest.raises(InvalidInput, match="unselected_source"):
        hybrid_candidates(
            prepared.request, "资料", ["note:unknown"], [0.5] * len(prepared.request.notes)
        )
