"""Content-free exit diagnostics for the isolated processing worker."""

import sys
from pathlib import Path

from .media_processing_capture import capture


def main() -> int:
    try:
        capture(Path("/media/input"), Path("/media/output"))
    except Exception:
        print("media_processing_rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
