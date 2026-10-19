"""Worker-only plaintext Bearer credential loading for explicit loopback metadata checks."""

import re
from pathlib import Path

from .config import InvalidInput
from .query_routing_files import read


def load(path: Path) -> tuple[str | None, bool | None, str | None]:
    if path.parent.name == "model-secrets" and path.suffix == ".json":
        from .model_configuration.errors import ModelError
        from .model_configuration.secrets import load as managed

        try:
            return managed(path) or None, True, None
        except ModelError:
            return None, False, "credential_file_rejected"
    """Return key, read status and safe failure code; never convert or copy secret files."""
    if path.suffix.lower() == ".rtf":
        return None, False, "credential_format_unsupported"
    try:
        raw = read(path, 4098)
    except (OSError, InvalidInput):
        # Failure may occur after a partial read or a snapshot change; do not guess.
        return None, None, "credential_file_rejected"
    try:
        key = raw.decode("ascii").strip(" \t\r\n")
    except UnicodeError:
        return None, True, "credential_invalid"
    if not 1 <= len(key) <= 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", key):
        return None, True, "credential_invalid"
    return key, True, None
