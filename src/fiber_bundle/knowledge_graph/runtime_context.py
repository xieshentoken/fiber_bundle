"""Verify immutable worker artifacts before importing the optional analysis SDK."""

import hashlib
import importlib.util
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import InvalidInput
from .contracts import SEMANTICA_REVISION, Model, fingerprint, object_fields, read_json

ALGORITHM = "body_topics_v5"
GRAPH_MINIMUM_SCORE = 0.70
QUERY_MINIMUM_SCORE = 0.52
NEIGHBOR_LIMIT = 20


def file_hashes(root: Path, *, python_only: bool = False) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise InvalidInput("graph_runtime_directory_invalid")
    result = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or (python_only and path.suffix != ".py"):
            continue
        if path.is_symlink():
            raise InvalidInput("graph_runtime_symlink")
        if path.is_dir():
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 512 * 1024**2:
            raise InvalidInput("graph_runtime_file_invalid")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        result[path.relative_to(root).as_posix()] = digest.hexdigest()
        if len(result) > 4000:
            raise InvalidInput("graph_runtime_file_limit")
    return result


def verify(context_path: Path, model_root: Path, model: Model | None) -> dict[str, Any]:
    context = object_fields(
        read_json(context_path.read_bytes(), 1024 * 1024),
        {"schema", "engine", "algorithm", "model", "model_files", "sdk_files_sha256"},
    )
    if (
        context["schema"] != 1
        or context["engine"]
        != {"name": "semantica", "version": "0.7.0", "revision": SEMANTICA_REVISION}
        or context["algorithm"] != ALGORITHM
        or model is None
        or context["model"] != asdict(model)
    ):
        raise InvalidInput("graph_runtime_binding_mismatch")
    model.validate()
    files = file_hashes(model_root)
    if context["model_files"] != files or fingerprint(files) != model.files_sha256:
        raise InvalidInput("graph_model_artifacts_changed")
    spec = importlib.util.find_spec("semantica")
    if spec is None or spec.origin is None:
        raise InvalidInput("graph_sdk_unavailable")
    if (
        fingerprint(file_hashes(Path(spec.origin).parent, python_only=True))
        != context["sdk_files_sha256"]
    ):
        raise InvalidInput("graph_sdk_artifacts_changed")
    return context
