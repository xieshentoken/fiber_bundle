"""Minimum proposal shape and exact source-span membership before canonical validation."""

from typing import Annotated, Any, Literal

from pydantic import Field

from .config import InvalidInput
from .objects import digest
from .payloads import Payload


class Span(Payload):
    page: Annotated[int, Field(strict=True, ge=1)]
    start: Annotated[int, Field(strict=True, ge=0)]
    end: Annotated[int, Field(strict=True, ge=1)]
    span_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Applicability(Payload):
    physical_pages: list[Annotated[int, Field(strict=True, ge=1)]] = Field(
        min_length=2, max_length=2
    )
    excluded_conclusions: list[str] = Field(max_length=100)


class Object(Payload):
    local_id: str = Field(min_length=1, max_length=100)
    type: Literal[
        "Definition",
        "Concept",
        "Claim",
        "Equation",
        "Assumption",
        "Exception",
        "Method",
        "Procedure",
        "DecisionRule",
    ]
    origin: Literal["source-explicit", "source-paraphrase", "compiler-derived"]
    title: str = Field(min_length=1, max_length=1000)
    statement: str = Field(min_length=1, max_length=20000)
    evidence_spans: list[Span] = Field(min_length=1, max_length=100)
    assumptions: list[str] = Field(max_length=100)
    valid_when: list[str] = Field(max_length=100)
    fails_when: list[str] = Field(max_length=100)
    applicability: Applicability
    visual_task_ids: list[str] = Field(max_length=100)
    formula: dict[str, Any] | None


class Disposition(Payload):
    action: Literal["promote", "context-only", "non-knowledge", "reject"]
    reason: str = Field(min_length=1, max_length=4000)


class Proposal(Payload):
    schema_version: Literal["tkc.semantic-draft/v0.1"]
    unit_id: str = Field(pattern=r"^swu-[a-f0-9]{20}$")
    proposer_instance: str = Field(min_length=1, max_length=200)
    candidate_disposition: Disposition
    objects: list[Object] = Field(max_length=100)
    relations: list[dict[str, Any]] = Field(max_length=100)
    conflicts: list[dict[str, Any]] = Field(max_length=100)
    gaps: list[dict[str, Any]] = Field(max_length=100)


def validate(value: object, unit: dict[str, Any], run: str) -> dict[str, Any]:
    proposal = Proposal.model_validate(value)
    if proposal.unit_id != unit["unit_id"]:
        raise InvalidInput("expert_authoring_unit_invalid")
    focus = unit["focus_spans"]
    for obj in proposal.objects:
        if not set(obj.visual_task_ids) <= set(unit["visual_task_ids"]):
            raise InvalidInput("expert_authoring_proposal_invalid")
        for span in obj.evidence_spans:
            matching = [
                f
                for f in focus
                if f["page"] == span.page and f["start"] <= span.start < span.end <= f["end"]
            ]
            if (
                len(matching) != 1
                or digest(
                    matching[0]["text"][
                        span.start - matching[0]["start"] : span.end - matching[0]["start"]
                    ].encode()
                )
                != span.span_sha256
            ):
                raise InvalidInput("expert_authoring_source_changed")
        pages = obj.applicability.physical_pages
        if pages[0] > pages[1] or any(
            s.page < pages[0] or s.page > pages[1] for s in obj.evidence_spans
        ):
            raise InvalidInput("expert_authoring_proposal_invalid")
    result = proposal.model_dump()
    # This is a proposal identity. It never acts as a semantic reviewer.
    result["proposer_instance"] = "fiber-pi:" + run
    return result


def fragments(unit: dict[str, Any]) -> list[dict[str, Any]]:
    parts = []
    for focus in unit["focus_spans"]:
        text = focus["text"]
        for offset in range(0, len(text), 3000):
            part = text[offset : offset + 3000]
            span = {
                **focus,
                "start": focus["start"] + offset,
                "end": focus["start"] + offset + len(part),
                "text": part,
                "span_sha256": digest(part.encode()),
            }
            parts.append({**unit, "focus_spans": [span], "context_spans": []})
    return parts or [{**unit, "focus_spans": [], "context_spans": []}]


def combine(unit: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    gaps = []
    for number, part in enumerate(parts):
        rename = {o["local_id"]: f"p{number}-{o['local_id']}" for o in part["objects"]}

        def ref(value: str, rename: dict[str, str] = rename) -> str:
            prefix = unit["unit_id"] + ":"
            key = value.removeprefix(prefix)
            return prefix + rename[key] if key in rename else value

        objects.extend({**o, "local_id": rename[o["local_id"]]} for o in part["objects"])
        relations.extend(
            {**r, "source_ref": ref(r["source_ref"]), "target_ref": ref(r["target_ref"])}
            for r in part["relations"]
        )
        conflicts.extend(
            {
                **c,
                "local_id": f"p{number}-" + c["local_id"],
                "claim_refs": [ref(r) for r in c["claim_refs"]],
            }
            for c in part["conflicts"]
        )
        gaps.extend(part["gaps"])
    return {
        "schema_version": "tkc.semantic-draft/v0.1",
        "unit_id": unit["unit_id"],
        "proposer_instance": "fiber-pi:composite-proposals",
        "candidate_disposition": {
            "action": "promote" if objects else "context-only",
            "reason": "Source-bound proposals; independent semantic review deferred.",
        },
        "objects": objects,
        "relations": relations,
        "conflicts": conflicts,
        "gaps": gaps,
    }
