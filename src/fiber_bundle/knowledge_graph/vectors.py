"""Bounded v2 build envelope for verified, source/version-scoped vector reuse."""

import math
from dataclasses import dataclass
from typing import Any

from ..config import InvalidInput
from ..inputs import canonical
from .contracts import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    Request,
    decode_request,
    object_fields,
    read_json,
)
from .results import Result, decode_result

SCHEMA = "fiber-knowledge-build/v2"


def validate(value: Any, request: Request, *, complete: bool = False) -> dict[str, list[float]]:
    if not isinstance(value, dict) or len(value) > len(request.notes):
        raise InvalidInput("graph_invalid_vector_cache")
    notes = {n.note_id: n for n in request.notes}
    model = request.engine.model
    if (model is None and value) or (complete and model is not None and set(value) != set(notes)):
        raise InvalidInput("graph_invalid_vector_cache")
    result = {}
    for identity, item in value.items():
        row = object_fields(item, {"sha256", "text_sha256", "configuration_sha256", "values"})
        if identity not in notes or model is None:
            raise InvalidInput("graph_invalid_vector_cache")
        note = notes[identity]
        vector = row["values"]
        if (
            (row["sha256"], row["text_sha256"], row["configuration_sha256"])
            != (note.sha256, note.text_sha256, request.configuration_sha256)
            or not isinstance(vector, list)
            or len(vector) != model.dimensions
            or any(type(v) not in {float, int} or not math.isfinite(v) for v in vector)
            or abs(sum(v * v for v in vector) - 1) > 0.003
        ):
            raise InvalidInput("graph_invalid_vector_cache")
        result[identity] = [float(v) for v in vector]
    return result


def rows(request: Request, vectors: dict[str, list[float]]) -> dict[str, Any]:
    value = {
        n.note_id: {
            "sha256": n.sha256,
            "text_sha256": n.text_sha256,
            "configuration_sha256": request.configuration_sha256,
            "values": vectors[n.note_id],
        }
        for n in request.notes
        if n.note_id in vectors
    }
    validate(value, request)
    return value


def encode(request: Request, cache: dict[str, Any]) -> bytes:
    validate(cache, request)
    raw = canonical(
        {
            "schema": SCHEMA,
            "request": read_json(request.encode(), MAX_INPUT_BYTES),
            "cached_vectors": cache,
        }
    ).encode()
    read_json(raw, MAX_INPUT_BYTES)
    return raw


def decode(raw: bytes) -> tuple[Request, dict[str, list[float]]]:
    value = object_fields(read_json(raw, MAX_INPUT_BYTES), {"schema", "request", "cached_vectors"})
    if value["schema"] != SCHEMA:
        raise InvalidInput("graph_unsupported_schema")
    request = decode_request(canonical(value["request"]).encode())
    return request, validate(value["cached_vectors"], request)


@dataclass(frozen=True)
class Build:
    result: Result
    raw_result: bytes
    vectors: dict[str, Any]
    embedded_count: int


def decode_build(raw: bytes, request: Request, cache: dict[str, Any]) -> Build:
    value = object_fields(
        read_json(raw, MAX_OUTPUT_BYTES), {"schema", "result", "vectors", "embedded_count"}
    )
    if value["schema"] != SCHEMA:
        raise InvalidInput("graph_unsupported_schema")
    validate(value["vectors"], request, complete=True)
    expected = len(request.notes) - len(cache) if request.engine.model else 0
    if type(value["embedded_count"]) is not int or value["embedded_count"] != expected:
        raise InvalidInput("graph_invalid_embedding_count")
    # Cached rows may not be silently recomputed or replaced by the worker.
    if any(value["vectors"].get(key) != row for key, row in cache.items()):
        raise InvalidInput("graph_vector_cache_changed")
    body = canonical(value["result"]).encode()
    return Build(decode_result(body, request), body, value["vectors"], value["embedded_count"])
