"""Content-free diagnostics for public platform acquisition."""

import sys
from pathlib import Path

from .media_files import decode
from .media_link_capture import capture
from .media_link_failure import record
from .media_processing_files import read


def main() -> int:
    try:
        spec = decode(read(Path("/media/input"), "request.json", 4096))
        if spec.get("kind") == "metadata_preflight":
            from .media_link_preflight_capture import capture as preflight

            preflight(Path("/media/input"), Path("/media/output"))
        else:
            capture(Path("/media/input"), Path("/media/output"))
    except Exception as error:
        try:
            record(Path("/media/input"), Path("/media/output"), error)
        except Exception:
            print("media_link_diagnostic_unavailable", file=sys.stderr)
        print("media_link_access_blocked", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
