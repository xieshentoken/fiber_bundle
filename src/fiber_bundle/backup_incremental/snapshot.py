"""Encrypted complete manifests reference shared file objects, without a linear delta chain."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .. import backup_preflight
from ..backup_bundle import file_sha, fresh_path, outside_sources, snapshot_manifest, vault_root
from ..backup_crypto import CHUNK
from ..backup_inventory import Limits
from ..backup_progress import Progress, report
from ..config import Config, IntegrityError, InvalidInput, contained, sync_directory
from ..inputs import canonical
from . import objects
from .repository import Repository, digest, private_file, uuid

SCHEMA = "encrypted-incremental/v2"
MAX_MANIFEST = 64 * 1024**2


def read(bundle: Path, password: str) -> tuple[Repository, dict[str, Any]]:
    from ..backup_bundle import layout

    marker = layout(bundle)
    if marker["schema"] != SCHEMA:
        raise InvalidInput("backup_incremental_format_required")
    repo = Repository(bundle.parent, password, marker["namespace"])
    with contained(bundle, "snapshot.fbenc").open("rb") as handle:
        reader = repo.records(handle, write=False)
        header = json.loads(reader.get())
        size = header.get("bytes")
        if header.get("schema") != SCHEMA or type(size) is not int or not 0 < size <= MAX_MANIFEST:
            raise IntegrityError("restore_manifest_invalid")
        remaining, chunks = size, []
        while remaining:
            chunk = reader.get()
            if len(chunk) != min(CHUNK, remaining):
                raise IntegrityError("restore_manifest_invalid")
            chunks.append(chunk)
            remaining -= len(chunk)
        wire = b"".join(chunks)
        sha = hashlib.sha256(wire).hexdigest()
        if sha != header.get("sha256"):
            raise IntegrityError("restore_manifest_invalid")
        reader.finish(sha)
    value = json.loads(wire)
    original = value["manifest"]
    if (
        value.get("schema") != SCHEMA
        or value.get("namespace") != repo.namespace
        or original.get("bundle_id") != marker["bundle_id"]
        or original.get("sha256") != marker["scope_sha256"]
    ):
        raise IntegrityError("restore_manifest_invalid")
    files = [entry for entry in original["entries"] if entry["kind"] == "file"]
    if len(files) != len(value["references"]) or len(files) > 1000000:
        raise IntegrityError("restore_manifest_invalid")
    refs = value["references"]
    for entry, identity in zip(files, refs, strict=True):
        if (
            repo.identity(entry["sha256"], entry["bytes"]) != digest(identity)
            or identity not in value["objects"]
        ):
            raise IntegrityError("restore_manifest_invalid")
        info = value["objects"][identity]
        digest(info["cipher_sha256"])
    if set(refs) != set(value["objects"]):
        raise IntegrityError("restore_manifest_invalid")
    return repo, value


def verify(
    bundle: Path, password: str, *, full: bool = True, progress: Progress | None = None
) -> None:
    repo, value = read(bundle, password)
    seen: set[str] = set()
    files = [entry for entry in value["manifest"]["entries"] if entry["kind"] == "file"]
    total = sum(e["bytes"] for e in files)
    done = 0
    report(
        progress, "verifying", files_done=0, files_total=len(files), bytes_done=0, bytes_total=total
    )
    for index, (entry, identity) in enumerate(zip(files, value["references"], strict=True), 1):
        if identity not in seen:
            objects.check(repo, identity, entry, value["objects"][identity], full=full)
            seen.add(identity)
        done += entry["bytes"]
        report(
            progress,
            "verifying",
            files_done=index,
            files_total=len(files),
            bytes_done=done,
            bytes_total=total,
        )
    report(
        progress,
        "verifying",
        files_done=len(files),
        files_total=len(files),
        bytes_done=done,
        bytes_total=total,
        complete=True,
    )


def create(
    config: Config,
    destination: Path,
    password: str,
    *,
    base: Path | None = None,
    limits: Limits = backup_preflight.DEFAULT_LIMITS,
    online: bool = False,
    capacity_bytes: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    destination = fresh_path(destination)
    previous: dict[str, Any] = {}
    if base is not None:
        repo, previous = read(base, password)
        if repo.root != destination.parent:
            raise InvalidInput("backup_incremental_repository_invalid")
    captured: dict[str, Any] = {}
    new_files = new_bytes = new_ciphertext_bytes = 0
    current_objects: dict[str, Any] = {}
    references = []
    source_overrides: dict[str, Path] = {}
    with backup_preflight.frozen(
        config,
        limits=limits,
        online=online,
        inventory_cache=previous.get("source_cache"),
        captured_cache=captured,
        progress=progress,
        source_overrides=source_overrides,
    ) as observed:
        outside_sources(config, destination, observed)
        if base is None:
            repo = Repository(destination.parent, password)
        roots = {
            "state": config.state_root,
            "configuration": config.project_root,
            **{"vault:" + v["id"]: vault_root(config, v) for v in observed["vaults"]},
        }
        manifest = snapshot_manifest(config, observed, roots)
        pending = [entry for entry in manifest["entries"] if entry["kind"] == "file"]
        required = sum(
            e["bytes"] + 2048
            for e in pending
            if not repo.path(repo.identity(e["sha256"], e["bytes"])).exists()
        )
        # A bounded metadata reserve is included before any new object is written.
        required += min(MAX_MANIFEST, len(canonical(manifest).encode()) * 5 + 1024**2)
        if capacity_bytes is not None and required > capacity_bytes:
            raise InvalidInput("backup_capacity_limit")
        if shutil.disk_usage(repo.home).free < required + 16 * 1024**2:
            raise InvalidInput("backup_disk_full")
        done = 0
        report(
            progress,
            "encrypting",
            files_done=0,
            files_total=len(pending),
            bytes_done=0,
            bytes_total=observed["file_bytes"],
        )
        for index, entry in enumerate(pending, 1):
            identity = repo.identity(entry["sha256"], entry["bytes"])
            if identity not in current_objects:
                _, info, created = objects.save(
                    repo,
                    source_overrides.get(
                        entry["path"], contained(roots[entry["section"]], entry["path"])
                    )
                    if entry["section"] == "state"
                    else contained(roots[entry["section"]], entry["path"]),
                    entry,
                    previous.get("objects", {}).get(identity),
                )
                current_objects[identity] = info
                if created:
                    new_files += 1
                    new_bytes += entry["bytes"]
                    new_ciphertext_bytes += repo.path(identity).stat().st_size
            references.append(identity)
            done += entry["bytes"]
            report(
                progress,
                "encrypting",
                files_done=index,
                files_total=len(pending),
                bytes_done=done,
                bytes_total=observed["file_bytes"],
            )
        report(
            progress,
            "encrypting",
            files_done=len(pending),
            files_total=len(pending),
            bytes_done=done,
            bytes_total=observed["file_bytes"],
            complete=True,
        )
        sync_directory(repo.home)
        value = {
            "schema": SCHEMA,
            "namespace": repo.namespace,
            "manifest": manifest,
            "objects": current_objects,
            "references": references,
            "source_cache": captured["files"],
        }
        wire = canonical(value).encode()
        if len(wire) > MAX_MANIFEST:
            raise InvalidInput("backup_manifest_limit")
        destination.mkdir(mode=0o700)
        fd = os.open(
            destination / "snapshot.fbenc",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "wb") as handle:
            writer = repo.records(handle, write=True)
            sha = hashlib.sha256(wire).hexdigest()
            writer.put(canonical({"schema": SCHEMA, "bytes": len(wire), "sha256": sha}).encode())
            for offset in range(0, len(wire), CHUNK):
                writer.put(wire[offset : offset + CHUNK])
            writer.finish(sha)
            handle.flush()
            os.fsync(handle.fileno())
    marker = {
        "schema": SCHEMA,
        "bundle_id": manifest["bundle_id"],
        "state": "complete",
        "namespace": uuid(repo.namespace),
        "scope_sha256": observed["sha256"],
        "files": {"snapshot.fbenc": file_sha(destination / "snapshot.fbenc")},
    }
    private_file(destination / "bundle.json", canonical(marker).encode())
    sync_directory(destination)
    sync_directory(destination.parent)
    return {
        **marker,
        "stats": {
            "new_files": new_files,
            "new_bytes": new_bytes,
            "reused_files": len(pending) - new_files,
            "hashed_bytes": captured["hashed_bytes"],
            "reused_bytes": captured["reused_bytes"],
            "reused_content_bytes": observed["file_bytes"] - new_bytes,
            "new_ciphertext_bytes": new_ciphertext_bytes,
        },
    }
