"""Frozen synthetic evaluation. Missing comparators stay N/A; this is not production retrieval."""

import math
import re
from typing import Any

from ..config import InvalidInput
from .contracts import Request, fingerprint
from .results import Result
from .runtime_context import QUERY_MINIMUM_SCORE


def technical_ids(query: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(re.findall(r"(?<![A-Za-z0-9_-])[A-Z]{1,5}-[A-Z0-9]{1,5}(?![A-Za-z0-9_-])", query))
        )
    )


def eligible(query: str, text: str) -> bool:
    return all(
        re.search(r"(?<![A-Za-z0-9_-])" + re.escape(code) + r"(?![A-Za-z0-9_-])", text)
        for code in technical_ids(query)
    )


def hybrid_candidates(
    request: Request, query: str, literal: list[str], scores: list[float]
) -> list[str]:
    if len(scores) != len(request.notes):
        raise InvalidInput("graph_evaluation_score_shape")
    if any(
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not -1.00001 <= value <= 1.00001
        for value in scores
    ):
        raise InvalidInput("graph_evaluation_score_invalid")
    selected = {note.note_id: note for note in request.notes}
    if any(note_id not in selected for note_id in literal):
        raise InvalidInput("graph_evaluation_unselected_source")
    # Preserve the actual existing ranking; only fill remaining slots with semantic clues.
    result = list(dict.fromkeys(literal))[:10]
    if len(result) == 10:
        return result
    ordered = sorted(range(len(scores)), key=lambda i: (-scores[i], request.notes[i].note_id))
    for i in ordered:
        note = request.notes[i]
        if (
            note.note_id not in result
            and scores[i] >= QUERY_MINIMUM_SCORE
            and eligible(query, note.text)
        ):
            result.append(note.note_id)
        if len(result) >= 10:
            break
    return result


def report(
    request: Request,
    result: Result,
    corpus: dict[str, Any],
    labels: dict[str, Any],
    literal: dict[str, list[str]],
    scores: dict[str, list[float]],
) -> dict[str, Any]:
    request.validate()
    samples = {sample["text"]: sample for sample in corpus["notes"]}
    if len(samples) != len(corpus["notes"]) or any(
        note.text not in samples for note in request.notes
    ):
        raise InvalidInput("graph_evaluation_corpus_mismatch")
    by_id = {note.note_id: samples[note.text] for note in request.notes}
    sample_to_note = {sample["sample_id"]: note_id for note_id, sample in by_id.items()}
    entities = {entity.id: entity for entity in result.entities}
    neighbors: dict[str, list[tuple[float, str]]] = {note_id: [] for note_id in by_id}
    for relation in result.relations:
        a, b = (
            entities[relation.source].evidence.note_id,
            entities[relation.target].evidence.note_id,
        )
        score = relation.score if relation.score is not None else 0.0
        neighbors[a].append((score, b))
        neighbors[b].append((score, a))
    groups = labels["related_categories"]
    graph_rows = []
    for note_id, sample in by_id.items():
        if sample["split"] != "validation":
            continue
        related = next(group for group in groups if sample["category"] in group)
        gold = {
            other for other, s in by_id.items() if s["category"] in related and other != note_id
        }
        ranked = [
            target for _, target in sorted(neighbors[note_id], key=lambda v: (-v[0], v[1]))[:10]
        ]
        found = len(gold.intersection(ranked))
        graph_rows.append(
            {
                "sample_id": sample["sample_id"],
                "suggestions": len(ranked),
                "valid": found,
                "precision_at_10": found / 10,
                "recall_at_10": found / len(gold),
            }
        )
    query_rows = []
    for question in labels["questions"]:
        query = question["query"]
        gold = {
            note_id
            for note_id, sample in by_id.items()
            if sample["category"] in question["categories"]
        }
        old = literal[query]
        new = hybrid_candidates(request, query, old, scores[query])
        row = {
            "id": question["id"],
            "split": question["split"],
            "query": query,
            "gold": len(gold),
            "literal_ids": old,
            "candidate_ids": new,
            "new_valid_ids": sorted((set(new) - set(old)) & gold),
        }
        for name, values in (("literal", old), ("candidate", new)):
            valid = len(set(values) & gold)
            row[name] = {
                "returned": len(values),
                "valid": valid,
                "precision_at_10": valid / 10,
                "recall_at_10": valid / len(gold) if gold else None,
                "false_hits": len(values) - valid,
            }
        query_rows.append(row)
    validation = [row for row in query_rows if row["split"] == "validation"]
    association_cases = []
    all_pairs = {
        frozenset((entities[r.source].evidence.note_id, entities[r.target].evidence.note_id))
        for r in result.relations
    }
    for annotation in corpus["annotations"]:
        ids = [sample_to_note[sample_id] for sample_id in annotation["notes"]]
        expectation = annotation["expectation"]
        if expectation.startswith("related"):
            passed = frozenset(ids) in all_pairs
        elif expectation == "unrelated":
            passed = frozenset(ids) not in all_pairs
        elif expectation == "do_not_merge":
            passed = all(
                any(e.evidence.note_id == note_id for e in result.entities) for note_id in ids
            )
        else:
            passed = not any(
                r.kind == "source_explicit" and any(span.note_id in ids for span in r.evidence)
                for r in result.relations
            )
        association_cases.append(
            {"id": annotation["id"], "expectation": expectation, "passed": passed}
        )
    no_degradation = all(
        row["candidate"]["valid"] >= row["literal"]["valid"]
        and row["candidate"]["false_hits"] <= row["literal"]["false_hits"]
        for row in validation
    )
    gain = sum(len(row["new_valid_ids"]) for row in validation)
    gate = no_degradation and gain > 0 and all(case["passed"] for case in association_cases)
    return {
        "schema": "fiber-graph-s1-evaluation/v1",
        "input_sha256": request.input_sha256,
        "labels_sha256": fingerprint(labels),
        "annotation_review": labels["independent_human_review"],
        "association_comparator": "N/A: no existing graph relation ranking",
        "validation_association_rows": graph_rows,
        "annotation_cases": association_cases,
        "queries": query_rows,
        "validation_new_valid_recall": gain,
        "validation_no_degradation": no_degradation,
        "synthetic_s1_gate": "PASS" if gate else "FAIL",
        "real_vault_gate": "NOT RUN",
        "candidate_semantics": "source-grounded clues, not independently verified facts",
    }
