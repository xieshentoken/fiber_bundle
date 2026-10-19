"""Fixed offline container entrypoint. SDK logs/body never enter the protocol or diagnostics."""

import contextlib
import os
import sys
from pathlib import Path

from ..config import InvalidInput
from ..inputs import canonical
from . import query_protocol, vectors
from .contracts import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, decode_request, read_json

FAILURES = frozenset(
    {
        "graph_worker_failed",
        "graph_real_vector_backend_unavailable",
        "graph_model_artifacts_changed",
        "graph_sdk_artifacts_changed",
        "graph_sdk_unavailable",
        "graph_runtime_binding_mismatch",
        "graph_invalid_real_vectors",
        "graph_adapter_unsupported",
        "graph_title_mention_unavailable",
        "graph_body_unavailable",
        "graph_model_input_limit",
    }
)


def main() -> int:
    try:
        raw_input = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if read_json(raw_input, MAX_INPUT_BYTES).get("schema") == query_protocol.SCHEMA:
            request, query, cache = query_protocol.decode(raw_input)
            from .semantica_adapter import query_scores

            with (
                open(os.devnull, "w") as quiet,
                contextlib.redirect_stdout(quiet),
                contextlib.redirect_stderr(quiet),
            ):
                scores = query_scores(
                    request, Path("/opt/graph/context.json"), Path("/opt/graph/model"), query, cache
                )
            from .contracts import fingerprint

            sys.stdout.buffer.write(
                canonical(
                    {
                        "schema": query_protocol.SCHEMA,
                        "input_sha256": request.input_sha256,
                        "query_sha256": fingerprint(query),
                        "scores": scores,
                    }
                ).encode()
            )
            return 0
        incremental = read_json(raw_input, MAX_INPUT_BYTES).get("schema") == vectors.SCHEMA
        request, cache = (
            vectors.decode(raw_input) if incremental else (decode_request(raw_input), {})
        )
        from .semantica_adapter import analyze_cached

        with (
            open(os.devnull, "w") as quiet,
            contextlib.redirect_stdout(quiet),
            contextlib.redirect_stderr(quiet),
        ):
            value, output_vectors, count = analyze_cached(
                request, Path("/opt/graph/context.json"), Path("/opt/graph/model"), cache
            )
        if incremental:
            value = {
                "schema": vectors.SCHEMA,
                "result": value,
                "vectors": vectors.rows(request, output_vectors),
                "embedded_count": count,
            }
        raw = canonical(value).encode()
        if len(raw) > MAX_OUTPUT_BYTES:
            raise InvalidInput("graph_output_limit")
        sys.stdout.buffer.write(raw)
        return 0
    except Exception as exc:
        # Untrusted SDK exceptions can include source text, local paths or provider errors.
        code = (
            str(exc)
            if isinstance(exc, InvalidInput) and str(exc) in FAILURES
            else "graph_worker_failed"
        )
        sys.stderr.write(code + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
