"""Explicit offline S1 benchmark entry; not a query route or a scheduled production task."""

import contextlib
import os
import sys
from pathlib import Path

from ..inputs import canonical
from .contracts import MAX_INPUT_BYTES, decode_request, fingerprint, object_fields, read_json


def main() -> int:
    try:
        value = object_fields(
            read_json(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1), MAX_INPUT_BYTES),
            {"request", "queries"},
        )
        request = decode_request(canonical(value["request"]).encode())
        if not isinstance(value["queries"], list):
            raise ValueError("invalid query set")
        from .semantica_adapter import benchmark_scores

        with (
            open(os.devnull, "w") as quiet,
            contextlib.redirect_stdout(quiet),
            contextlib.redirect_stderr(quiet),
        ):
            scores = benchmark_scores(
                request,
                Path("/opt/graph/context.json"),
                Path("/opt/graph/model"),
                tuple(value["queries"]),
                enabled=True,
            )
        sys.stdout.write(
            canonical(
                {
                    "input_sha256": request.input_sha256,
                    "query_set_sha256": fingerprint(value["queries"]),
                    "scores": scores,
                }
            )
        )
        return 0
    except Exception:
        sys.stderr.write("graph_benchmark_failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
