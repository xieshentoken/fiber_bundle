"""Validate untrusted graph candidates against frozen spans; no acceptance or writeback."""

import math
from dataclasses import asdict, dataclass
from typing import Any

from ..config import InvalidInput
from .contracts import (
    MAX_ENTITIES,
    MAX_NOTES,
    MAX_OUTPUT_BYTES,
    MAX_RELATIONS,
    SCHEMA,
    Request,
    bounded_text,
    fingerprint,
    object_fields,
    read_json,
)

DERIVATIONS = {
    "source_explicit": "source_statement",
    "cooccurrence": "literal_cooccurrence",
    "similarity": "semantic_similarity",
    "hypothesis": "topology_hypothesis",
}


@dataclass(frozen=True)
class Span:
    note_id: str
    sha256: str
    start: int
    end: int
    line: int
    line_end: int
    quote: str


@dataclass(frozen=True)
class Entity:
    id: str
    label: str
    evidence: Span


@dataclass(frozen=True)
class Relation:
    id: str
    source: str
    target: str
    kind: str
    derivation: str
    method: str
    score: float | None
    score_kind: str
    evidence: tuple[Span, ...]


@dataclass(frozen=True)
class MocSuggestion:
    title: str
    note_ids: tuple[str, ...]
    evidence: tuple[Span, ...]
    method: str


@dataclass(frozen=True)
class Result:
    entities: tuple[Entity, ...]
    relations: tuple[Relation, ...]
    mocs: tuple[MocSuggestion, ...]
    review_state: str = "suggested"


def line_at(text: str, offset: int) -> int:
    cursor = 0
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        cursor += len(line)
        if offset < cursor:
            return number
    raise InvalidInput("graph_span_offset_out_of_range")


def parse_span(value: Any, request: Request) -> Span:
    fields = object_fields(value, set(Span.__dataclass_fields__))
    span = Span(**fields)
    if not isinstance(span.note_id, str):
        raise InvalidInput("graph_unknown_source")
    note = next((note for note in request.notes if note.note_id == span.note_id), None)
    if note is None or span.sha256 != note.sha256:
        raise InvalidInput("graph_unknown_or_stale_source")
    bounded_text(span.quote, 400)
    if (
        any(type(v) is not int for v in (span.start, span.end, span.line, span.line_end))
        or not 0 <= span.start < span.end <= len(note.text)
        or note.text[span.start : span.end] != span.quote
        or span.line != line_at(note.text, span.start)
        or span.line_end != line_at(note.text, span.end - 1)
        or not 0 <= span.line_end - span.line <= 2
    ):
        raise InvalidInput("graph_invalid_source_span")
    return span


def entity_id(label: str, evidence: Span) -> str:
    # Homonyms in separate notes/spans stay separate; no string-based global entity merging.
    return fingerprint({"label": label, "evidence": asdict(evidence)})


def relation_id(source: str, target: str, kind: str, evidence: tuple[Span, ...]) -> str:
    return fingerprint(
        {
            "source": source,
            "target": target,
            "kind": kind,
            "evidence": [asdict(span) for span in evidence],
        }
    )


def spans(value: Any, request: Request, *, maximum: int = 8) -> tuple[Span, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise InvalidInput("graph_evidence_limit")
    result = tuple(parse_span(item, request) for item in value)
    if len({fingerprint(asdict(span)) for span in result}) != len(result):
        raise InvalidInput("graph_duplicate_evidence")
    return result


def candidates(value: Any, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise InvalidInput("graph_candidate_limit")
    return value


def decode_result(raw: bytes, request: Request) -> Result:
    request.validate()
    value = object_fields(
        read_json(raw, MAX_OUTPUT_BYTES),
        {
            "schema",
            "task_id",
            "input_sha256",
            "configuration_sha256",
            "engine",
            "entities",
            "relations",
            "mocs",
        },
    )
    if (
        value["schema"] != SCHEMA
        or value["task_id"] != request.task_id
        or value["input_sha256"] != request.input_sha256
        or value["configuration_sha256"] != request.configuration_sha256
        or value["engine"]
        != {
            "name": request.engine.name,
            "version": request.engine.version,
            "revision": request.engine.revision,
        }
    ):
        raise InvalidInput("graph_result_binding_mismatch")
    entities = []
    for item in candidates(value["entities"], MAX_ENTITIES):
        item = object_fields(item, set(Entity.__dataclass_fields__))
        bounded_text(item["label"], 200)
        mention = parse_span(item["evidence"], request)
        if item["label"] not in mention.quote or item["id"] != entity_id(item["label"], mention):
            raise InvalidInput("graph_invalid_entity_mention")
        entities.append(Entity(item["id"], item["label"], mention))
    by_id = {entity.id: entity for entity in entities}
    if len(by_id) != len(entities):
        raise InvalidInput("graph_duplicate_entity")
    relations = []
    for item in candidates(value["relations"], MAX_RELATIONS):
        item = object_fields(item, set(Relation.__dataclass_fields__))
        if (
            not isinstance(item["source"], str)
            or not isinstance(item["target"], str)
            or item["source"] not in by_id
            or item["target"] not in by_id
            or item["source"] == item["target"]
        ):
            raise InvalidInput("graph_unknown_relation_endpoint")
        if (
            not isinstance(item["kind"], str)
            or item["kind"] not in DERIVATIONS
            or item["derivation"] != DERIVATIONS[item["kind"]]
            or item["method"] != request.engine.algorithm
        ):
            raise InvalidInput("graph_unsupported_or_fallback_relation")
        if item["kind"] == "similarity" and request.engine.model is None:
            raise InvalidInput("graph_semantic_model_unconfigured")
        if not isinstance(item["score_kind"], str):
            raise InvalidInput("graph_invalid_score_kind")
        score = item["score"]
        if item["score_kind"] == "unscored":
            if score is not None:
                raise InvalidInput("graph_invalid_score")
        elif item["score_kind"] in {"model_score", "heuristic_score"}:
            if item["score_kind"] == "model_score" and request.engine.model is None:
                raise InvalidInput("graph_semantic_model_unconfigured")
            if type(score) not in {float, int} or not 0 <= score <= 1 or not math.isfinite(score):
                raise InvalidInput("graph_invalid_score")
        else:
            raise InvalidInput("graph_score_is_not_fact_probability")
        evidence = spans(item["evidence"], request)
        source, target = by_id[item["source"]], by_id[item["target"]]
        supported = {span.note_id for span in evidence}
        if not {source.evidence.note_id, target.evidence.note_id} <= supported:
            raise InvalidInput("graph_missing_endpoint_sources")
        if item["kind"] == "source_explicit" and not any(
            source.label in span.quote and target.label in span.quote for span in evidence
        ):
            raise InvalidInput("graph_missing_explicit_statement")
        if item["id"] != relation_id(source.id, target.id, item["kind"], evidence):
            raise InvalidInput("graph_invalid_relation_id")
        relations.append(Relation(**{**item, "evidence": evidence}))
    if len({relation.id for relation in relations}) != len(relations):
        raise InvalidInput("graph_duplicate_relation")
    mocs = []
    for item in candidates(value["mocs"], 100):
        item = object_fields(item, set(MocSuggestion.__dataclass_fields__))
        bounded_text(item["title"], 200)
        members = item["note_ids"]
        if (
            not isinstance(members, list)
            or not 2 <= len(members) <= MAX_NOTES
            or any(not isinstance(member, str) for member in members)
            or members != sorted(set(members))
            or item["method"] != request.engine.algorithm
        ):
            raise InvalidInput("graph_invalid_moc_suggestion")
        evidence = spans(item["evidence"], request, maximum=MAX_NOTES)
        if set(members) != {span.note_id for span in evidence}:
            raise InvalidInput("graph_moc_members_not_supported")
        mocs.append(MocSuggestion(item["title"], tuple(members), evidence, item["method"]))
    if len({fingerprint(asdict(moc)) for moc in mocs}) != len(mocs):
        raise InvalidInput("graph_duplicate_moc_suggestion")
    return Result(tuple(entities), tuple(relations), tuple(mocs))
