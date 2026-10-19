"""Owned offline container boundary for one finite video segment."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import video_contract
from .config import Config, contained, make_directory
from .document_source import DocumentFile
from .inputs import canonical
from .objects import digest
from .processing_container import run as run_container
from .query_policy_store import write_private
from .query_routing_files import read

container = run_container

TOOL_FILES = (
    "video_capture.py",
    "video_contract.py",
    "media_processing_capture.py",
    "media_processing_files.py",
    "media_transcript.py",
    "media_subtitles.py",
    "media_link_capture.py",
    "media_link_parts.py",
    "media_link_metadata.py",
    "video_link_entry.py",
    "media_glossary_contract.py",
    "processing_container.py",
)


def code_digest() -> str:
    root = Path(__file__).parent
    return digest(canonical({n: digest((root / n).read_bytes()) for n in TOOL_FILES}).encode())


def execute(
    config: Config,
    source: DocumentFile,
    spec: dict[str, Any],
    settings: dict[str, Any],
    guard: Callable[[], None],
) -> dict[str, Any]:
    guard()
    attempt = str(uuid4())
    root = contained(config.state_root, "video-segments/" + attempt)
    make_directory(root / "input")
    make_directory(root / "output")
    source.copy_new(root / "input/source")
    write_private(root / "input/request.json", canonical(spec).encode())
    container(
        root,
        attempt,
        settings["image"],
        "video_capture",
        settings["timeout_seconds"],
        guard,
        asr=spec["asr_resources"],
    )
    source.verify()
    guard()
    value = json.loads(read(root / "output/result.json", 2 * 1024 * 1024))
    video_contract.verify(value, spec)
    return {
        "attempt_id": attempt,
        "result_sha256": digest(canonical(value).encode()),
        "result": value,
    }
