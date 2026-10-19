"""Fixed MCP and managed-request entries; secrets stay in private referenced files."""

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["mcp", "operation", "request"])
    parser.add_argument("--connection-file", type=Path)
    parser.add_argument("--job-file", type=Path)
    parser.add_argument("--question")
    parser.add_argument("--query", action="append")
    args = parser.parse_args()
    try:
        if args.mode == "mcp":
            if args.connection_file is None:
                parser.error("--connection-file required")
            from .mcp_server import server

            server(args.connection_file).run(transport="stdio")
        elif args.mode == "operation":
            if args.job_file is None:
                parser.error("--job-file required")
            import signal

            from .worker import execute

            def interrupted(signum: int, _: object) -> None:
                raise SystemExit(128 + signum)

            signal.signal(signal.SIGTERM, interrupted)
            execute(args.job_file)
        else:
            if args.connection_file is None or args.question is None or not args.query:
                parser.error("--connection-file, --question and explicit --query required")
            from uuid import uuid4

            from .http_client import Client

            client = Client(args.connection_file)
            req = {
                "request_id": str(uuid4()),
                "question": args.question,
                "retrieval_queries": args.query,
            }
            operation = client.request("/v1/agent-requests", req)
            deadline = time.monotonic() + 180
            while operation["state"] in ("queued", "running") and time.monotonic() < deadline:
                time.sleep(0.3)
                operation = client.request("/v1/agent-requests/" + operation["id"])
            print(json.dumps(operation, ensure_ascii=False))
            if operation["state"] != "succeeded":
                raise SystemExit(1)
    except Exception:
        print("agent_request_failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
