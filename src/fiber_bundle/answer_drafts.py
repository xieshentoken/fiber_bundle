"""Cited-draft contract; citation fidelity does not establish semantic support."""

import re
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from .answer_contract import Quotation, SelectionRejected, validate_selection
from .answer_model_output import parse_response
from .config import InvalidInput
from .inputs import text
from .payloads import Payload


class DraftRejected(InvalidInput):
    def __init__(self, reason: str):
        self.code = "answer_draft_rejected_" + reason
        super().__init__(self.code)


class DraftClaim(Payload):
    text: Annotated[str, Field(min_length=1, max_length=600)]
    citations: Annotated[list[str], Field(min_length=1, max_length=20)]


class DraftProposal(Payload):
    mode: Literal["cited_draft"]
    claims: Annotated[list[DraftClaim], Field(max_length=8)]
    quotations: Annotated[list[Quotation], Field(max_length=20)]


def validate_draft(value: object, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Check structure and quotation identity only; always mark prose as unverified."""
    try:
        proposal = DraftProposal.model_validate(value)
    except ValidationError:
        raise DraftRejected("schema") from None
    if proposal.claims and not evidence:
        raise DraftRejected("claim_without_evidence")
    try:
        quotations = validate_selection(
            {"mode": "extractive", "quotations": [q.model_dump() for q in proposal.quotations]},
            evidence,
        )
    except SelectionRejected as exc:
        raise DraftRejected(exc.reason) from None
    available = {q["citation"] for q in quotations}
    claims = []
    for claim in proposal.claims:
        try:
            text(claim.text, limit=600)
        except InvalidInput:
            raise DraftRejected("text") from None
        # Citations are structured metadata, not model-authored presentation markup.
        if re.search(r"\[\s*\d+\s*\]", claim.text):
            raise DraftRejected("citation_in_text")
        if len(set(claim.citations)) != len(claim.citations):
            raise DraftRejected("duplicate_citation")
        if not set(claim.citations) <= available:
            raise DraftRejected("unknown_citation")
        claims.append(
            {
                "text": claim.text,
                "citations": claim.citations,
                "semantic_support": "not_verified",
            }
        )
    return {
        "mode": "cited_draft",
        "status": "unverified_draft" if claims else "insufficient_evidence",
        "claims": claims,
        "quotations": quotations,
        "citation_integrity": "verified",
        "semantic_sufficiency": "not_assessed",
        "conflict_assessment": "not_assessed",
        "freshness_assessment": "not_assessed",
        "cautions": [
            "模型草稿，语义未核验；引用一致不表示原文支持概括或足以回答问题。",
            "原文、用户修订与模型分析必须区分；不得将草稿当作已核验结论。",
            "时效与冲突未判断；保留全部检索片段，不以选择性引用消除差异。",
        ],
    }


def validate_draft_response(body: object, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return validate_draft(parse_response(body), evidence)
