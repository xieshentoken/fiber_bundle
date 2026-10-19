"""Canonical full compiler artifacts: bounded preservation, never semantic re-authoring."""

import json
import os
import stat
from pathlib import Path
from typing import Any

from .config import IntegrityError, InvalidInput, make_directory
from .document_source import DocumentFile
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private
from .query_routing_files import read

MAX_TREE = 32 * 1024 * 1024


def tree(root: Path) -> dict[str, bytes]:
    if root.resolve() != root or root.is_symlink() or not root.is_dir():
        raise IntegrityError("expert_artifact_path")
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if path.resolve() != path or stat.S_ISLNK(info.st_mode):
            raise IntegrityError("expert_artifact_path")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024:
            raise IntegrityError("expert_artifact_file")
        total += info.st_size
        if total > MAX_TREE or len(files) >= 2000:
            raise InvalidInput("expert_artifact_capacity")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(info.st_size + 1)
            after = os.fstat(stream.fileno())
        current = path.lstat()

        def identity(s: os.stat_result) -> tuple[int, ...]:
            return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink

        if len(data) != info.st_size or not identity(info) == identity(before) == identity(
            after
        ) == identity(current):
            raise IntegrityError("expert_artifact_changed")
        files[path.relative_to(root).as_posix()] = data
    return files


def manifest(files: dict[str, bytes]) -> dict[str, Any]:
    return {name: {"sha256": digest(raw), "byte_count": len(raw)} for name, raw in files.items()}


def copy_tree(source: Path, target: Path) -> None:
    files = tree(source)
    if target.exists():
        raise InvalidInput("expert_resume_destination_exists")
    make_directory(target)
    for name, raw in files.items():
        path = target / name
        make_directory(path.parent)
        write_private(path, raw)
    if manifest(tree(target)) != manifest(files):
        raise IntegrityError("expert_resume_copy_changed")


def capture_package(
    output: Path, capture: dict[str, Any], source: DocumentFile, plan: dict[str, Any]
) -> dict[str, Any]:
    job = json.loads(read(output / "workspace/fake-expert-job.json", 1024 * 1024))
    worker_root = Path(job["outputs"]["skill"])
    if not worker_root.is_relative_to(Path("/document/output/workspace")):
        raise IntegrityError("expert_package_scope")
    root = output / worker_root.relative_to("/document/output")
    files = tree(root)
    canonical_manifest = json.loads(files["manifest.json"])
    control = json.loads(files["references/runtime/direct-reference.json"])
    usage = control["usage"]
    if (
        canonical_manifest["package"]["status"] != "draft"
        or usage["review_state"] != "deferred"
        or usage["knowledge_verified"] is not False
        or capture.get("artifact_validation") is not True
    ):
        raise IntegrityError("expert_candidate_assurance")
    index = json.loads(files["references/knowledge/index.json"])
    objects = [json.loads(files[row["path"]]) for row in index["objects"]]
    anchors = [json.loads(line) for line in files["references/evidence/anchors.jsonl"].splitlines()]
    if not objects or not anchors:
        raise InvalidInput("expert_empty_candidate")
    limits = plan["limits"]
    markdown = [raw for name, raw in files.items() if name.endswith(".md")]
    if (
        any(len(raw) > limits["markdown_bytes"] for raw in markdown)
        or sum(map(len, markdown)) > limits["markdown_total_bytes"]
    ):
        raise InvalidInput("expert_markdown_capacity")
    result_bytes = sum(
        len(raw) for name, raw in files.items() if name.endswith((".json", ".jsonl"))
    )
    if result_bytes > limits["result_bytes"]:
        raise InvalidInput("expert_result_capacity")
    packet = {
        "schema": "fiber-expert-artifacts/v1",
        "source_sha256": source.sha256,
        "root": root.relative_to(output).as_posix(),
        "files": manifest(files),
        "object_count": len(objects),
        "anchor_count": len(anchors),
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
    }
    write_private(output / "artifacts.json", canonical(packet).encode())
    return packet
