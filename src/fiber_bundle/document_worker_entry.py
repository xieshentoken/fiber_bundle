"""Fixed offline worker: original PDF in /document/input, candidate in /document/output."""

import json
import logging
import sys
from pathlib import Path

from .config import StorageError
from .document_capture import capture, reject_duplicate
from .document_contract import DocumentError


def main() -> int:
    logging.getLogger("pypdf").disabled = True
    try:
        raw = sys.stdin.buffer.read(16385)
        if not 0 < len(raw) <= 16384:
            raise DocumentError("document_worker_request_size")
        request = json.loads(raw, object_pairs_hook=reject_duplicate)
        if type(request) is not dict or type(request.get("schema")) is not int:
            raise DocumentError("document_worker_request")
        runtime = None
        if request == {"schema": 1}:
            if len(raw) > 256:
                raise DocumentError("document_worker_request_size")
            capture(Path("/document/input"), Path("/document/output"))
            return 0
        elif (
            set(request) == {"schema", "format", "limits"}
            and request["schema"] == 3
            and request["format"] == "pdf"
        ):
            from .document_capacity import checked

            capture(
                Path("/document/input"), Path("/document/output"), limits=checked(request["limits"])
            )
            return 0
        elif (
            set(request) == {"schema", "format"}
            and request["schema"] == 2
            and isinstance(request["format"], str)
            and request["format"] in {"docx", "xlsx", "pptx", "chat", "text", "archive"}
        ):
            format = request["format"]
        elif (
            set(request) == {"schema", "format", "profile", "runtime"}
            and request["schema"] == 2
            and request["profile"] == "conversion"
            and request["format"] == "doc"
        ):
            from .document_conversion import verify_runtime as verify_conversion

            runtime = request["runtime"]
            verify_conversion(runtime)
            format = "doc"
        elif (
            set(request) == {"schema", "format", "profile", "runtime"}
            and request["schema"] == 2
            and request["profile"] == "ocr"
            and isinstance(request["format"], str)
            and request["format"] in {"pdf", "image"}
        ):
            from .document_ocr import verify_runtime

            runtime = request["runtime"]
            verify_runtime(runtime)
            format = request["format"]
        else:
            raise DocumentError("document_worker_request")
        capture(Path("/document/input"), Path("/document/output"), format, runtime)
        return 0
    except (DocumentError, StorageError, OSError, ValueError, RecursionError):
        sys.stderr.write("document_capture_failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
