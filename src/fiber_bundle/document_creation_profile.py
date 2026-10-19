"""Bundled, narrow synthetic file qualification. Never accept an operator's capability claim."""

from pathlib import Path
from typing import Any

from .config import InvalidInput
from .document_creation_config import CreationExecutionConfig
from .objects import digest

IMAGE = "sha256:1c7f202f5188146b649066e6c162c84a64f068d7a8cf72b65b3298df5c89c20b"
REVISION = "pi-0871-draft-v1"
SOURCES = {
    "bridge.mjs": "189e4bb4d9947776099e2994b4070a925e756b491dafcb592e6d1dcc9782403d",
    "draft_tool.mjs": "8ff68f753a3e01aec82ec8f206b87b4de94d02845667bf5d33a4239bbec3beec",
}


def value(mode: str = "synthetic", *, registered: bool = False) -> dict[str, Any]:
    if registered:
        from .model_configuration.runtime import manifest

        runtime = manifest()
        return {
            **value(mode),
            "image": runtime["creation_image"],
            "revision": runtime["creation_revision"],
            "sources": runtime["sources"],
        }
    return {
        "schema": "document-creation-provider-candidate/v1"
        if mode == "provider"
        else "document-creation-synthetic-profile/v1",
        "image": IMAGE,
        "revision": REVISION,
        "pi_version": "0.87.1",
        "formats": ["md", "txt"],
        "sources": dict(SOURCES),
        "max_rounds": 2,
        "real_provider_allowed": mode == "provider",
        **({"provider_qualification": "candidate_not_verified"} if mode == "provider" else {}),
    }


def check(
    policy: CreationExecutionConfig, requested_format: str, mode: str = "synthetic"
) -> dict[str, Any]:
    if hasattr(policy.policy(), "configuration_id"):
        from .model_configuration.runtime import require

        expected = value(mode, registered=True)
        if (
            policy.policy().image != expected["image"]
            or policy.capability_revision != expected["revision"]
            or requested_format not in {"md", "txt"}
            or requested_format not in policy.formats
        ):
            raise InvalidInput("document_runtime_unqualified")
        require(policy.policy().image)
        return expected
    if (
        mode not in {"synthetic", "provider"}
        or policy.policy().image != IMAGE
        or policy.capability_revision != REVISION
        or requested_format not in {"md", "txt"}
        or requested_format not in policy.formats
    ):
        raise InvalidInput("document_runtime_unqualified")
    root = Path(__file__).parent / "creation_executor"
    try:
        matches = all(digest((root / name).read_bytes()) == sha for name, sha in SOURCES.items())
    except OSError:
        raise InvalidInput("document_runtime_sources_unavailable") from None
    if not matches:
        raise InvalidInput("document_runtime_sources_changed")
    return value(mode)
