"""Explicit bounded query protocol; returned scores are navigation clues, never factual evidence."""

from typing import Any

from ..config import InvalidInput
from ..inputs import canonical
from .contracts import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    Request,
    bounded_text,
    decode_request,
    fingerprint,
    object_fields,
    read_json,
)
from .vectors import validate

SCHEMA = "fiber-knowledge-query/v1"


def encode(request: Request, query: str, cache: dict[str, Any]) -> bytes:
    bounded_text(query, 200)
    validate(cache, request, complete=True)
    if request.engine.model is None:
        raise InvalidInput("graph_query_model_unconfigured")
    raw = canonical(
        {
            "schema": SCHEMA,
            "request": read_json(request.encode(), MAX_INPUT_BYTES),
            "query": query,
            "cached_vectors": cache,
        }
    ).encode()
    read_json(raw, MAX_INPUT_BYTES)
    return raw


def decode(raw: bytes) -> tuple[Request, str, dict[str, list[float]]]:
    value = object_fields(
        read_json(raw, MAX_INPUT_BYTES), {"schema", "request", "query", "cached_vectors"}
    )
    if value["schema"] != SCHEMA:
        raise InvalidInput("graph_unsupported_schema")
    request = decode_request(canonical(value["request"]).encode())
    encode(request, value["query"], value["cached_vectors"])
    return request, value["query"], validate(value["cached_vectors"], request, complete=True)


def scores(raw: bytes, request: Request, query: str) -> list[float]:
    import math

    value = object_fields(
        read_json(raw, MAX_OUTPUT_BYTES), {"schema", "input_sha256", "query_sha256", "scores"}
    )
    result = value["scores"]
    if (
        value["schema"] != SCHEMA
        or value["input_sha256"] != request.input_sha256
        or value["query_sha256"] != fingerprint(query)
    ):
        raise InvalidInput("graph_query_binding_mismatch")
    if (
        not isinstance(result, list)
        or len(result) != len(request.notes)
        or any(
            type(s) not in {int, float} or not math.isfinite(s) or not -1.00001 <= s <= 1.00001
            for s in result
        )
    ):
        raise InvalidInput("graph_query_score_invalid")
    return [float(s) for s in result]
