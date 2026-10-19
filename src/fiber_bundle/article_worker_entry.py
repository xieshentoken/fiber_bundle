"""Fixed container entry point. No credentials, arbitrary command or output path inputs."""

import json
import sys
from pathlib import Path

from .article_capture import capture
from .article_fetch import fetch_public
from .articles import ArticleError


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read(8193))
        if not isinstance(request, dict) or set(request) != {"url"}:
            raise ArticleError("invalid_request")
        if not isinstance(request["url"], str) or len(request["url"]) > 4096:
            raise ArticleError("invalid_url")
        capture(request["url"], Path("/capture"), fetch_public)
        return 0
    except (ArticleError, OSError, ValueError):
        # Do not expose source/response text to the orchestrating model or Docker logs.
        sys.stderr.write("article_capture_failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
