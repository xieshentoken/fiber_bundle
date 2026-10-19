"""Offline complete local data backup with independently encrypted state and Vault sections."""

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from . import backup_preflight
from .backup_crypto import CHUNK, Records
from .backup_inventory import Limits
from .backup_progress import Progress, report
from .config import Config, IntegrityError, InvalidInput, contained, sync_directory
from .inputs import canonical
from .query_routing_files import read as bounded_read


def fresh_path(path: Path) -> Path:
    path = path.absolute()
    if path.parent.resolve(strict=True) != path.parent or path.exists() or path.is_symlink():
        raise InvalidInput("backup_destination_must_be_fresh_canonical")
    return path


def vault_root(config: Config, vault: dict[str, Any], *, restore: bool = False) -> Path:
    if vault.get("root_kind", "legacy") != "legacy" and not restore:
        return Path(vault["absolute_root"])
    return config.vault_path(vault["relative_path"])


def outside_sources(config: Config, path: Path, manifest: dict[str, Any]) -> None:
    for source in [
        config.state_root,
        *(vault_root(config, v) for v in manifest["vaults"]),
    ]:
        if path == source or source in path.parents or path in source.parents:
            raise InvalidInput("backup_destination_overlaps_source")


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise InvalidInput("backup_file_type_invalid")
        while data := handle.read(CHUNK):
            h.update(data)
    return h.hexdigest()


def section_entries(manifest: dict[str, Any], part: str) -> list[dict[str, Any]]:
    return [
        e for e in manifest["entries"] if e["section"].startswith("vault:") == (part == "vaults")
    ]


def snapshot_manifest(
    config: Config, observed: dict[str, Any], roots: dict[str, Path]
) -> dict[str, Any]:
    physical = []
    for entry in observed["entries"]:
        if entry["kind"] == "file" and entry["section"].startswith("vault:"):
            info = contained(roots[entry["section"]], entry["path"]).stat()
            birth = getattr(info, "st_birthtime", None)
            physical.append(
                {
                    "section": entry["section"],
                    "path": entry["path"],
                    "key": [
                        info.st_dev,
                        info.st_ino,
                        float(birth).hex() if birth is not None else None,
                    ],
                }
            )
    manifest = {
        **observed,
        "bundle_id": str(uuid4()),
        "bundle_schema": "encrypted-backup/v1",
        "physical_files": physical,
    }
    from .model_configuration.backup import export as export_models

    managed = export_models(config)
    if managed is not None:
        manifest["model_secrets"] = managed
    return manifest


def create(
    config: Config,
    destination: Path,
    password: str,
    *,
    expected_sha256: str | None = None,
    limits: Limits = backup_preflight.DEFAULT_LIMITS,
    include_configs: tuple[str, ...] = (),
    online: bool = False,
    capacity_bytes: int | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    destination = fresh_path(destination)
    source_overrides: dict[str, Path] = {}
    with backup_preflight.frozen(
        config,
        limits=limits,
        include_configs=include_configs,
        online=online,
        progress=progress,
        source_overrides=source_overrides,
    ) as observed:
        if expected_sha256 is not None and observed["sha256"] != expected_sha256:
            raise IntegrityError("backup_scope_changed")
        outside_sources(config, destination, observed)
        roots = {
            "state": config.state_root,
            "configuration": config.project_root,
            **{"vault:" + v["id"]: vault_root(config, v) for v in observed["vaults"]},
        }
        manifest = snapshot_manifest(config, observed, roots)
        managed = manifest.get("model_secrets")
        wire = canonical(manifest).encode()
        frames = sum((e.get("bytes", 0) + CHUNK - 1) // CHUNK for e in observed["entries"])
        required = observed["file_bytes"] + 2 * len(wire) + (frames + 4) * 20 + 8192
        if capacity_bytes is not None and required > capacity_bytes:
            raise InvalidInput("backup_capacity_limit")
        if shutil.disk_usage(destination.parent).free < required + 16 * 1024**2:
            raise InvalidInput("backup_disk_full")
        destination.mkdir(mode=0o700)
        sync_directory(destination.parent)
        total_files = sum(e["kind"] == "file" for e in observed["entries"])
        done_files = done_bytes = 0
        report(
            progress,
            "encrypting",
            files_done=0,
            files_total=total_files,
            bytes_done=0,
            bytes_total=observed["file_bytes"],
        )
        for part in ("state", "vaults"):
            path = destination / (part + ".fbenc")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as handle:
                records = Records(handle, password, write=True)
                records.put(wire)
                for entry in section_entries(manifest, part):
                    if entry["kind"] != "file":
                        continue
                    source = (
                        source_overrides.get(
                            entry["path"], contained(roots[entry["section"]], entry["path"])
                        )
                        if entry["section"] == "state"
                        else contained(roots[entry["section"]], entry["path"])
                    )
                    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as original:
                        info = os.fstat(original.fileno())
                        if (
                            not stat.S_ISREG(info.st_mode)
                            or info.st_nlink != 1
                            or info.st_size != entry["bytes"]
                        ):
                            raise IntegrityError("backup_source_changed")
                        digest = hashlib.sha256()
                        count = 0
                        while data := original.read(CHUNK):
                            count += len(data)
                            if count > entry["bytes"]:
                                raise IntegrityError("backup_source_changed")
                            digest.update(data)
                            records.put(data)
                        if count != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                            raise IntegrityError("backup_source_changed")
                    done_files += 1
                    done_bytes += entry["bytes"]
                    report(
                        progress,
                        "encrypting",
                        files_done=done_files,
                        files_total=total_files,
                        bytes_done=done_bytes,
                        bytes_total=observed["file_bytes"],
                    )
                records.finish(observed["sha256"])
                handle.flush()
                os.fsync(handle.fileno())
        report(
            progress,
            "encrypting",
            files_done=done_files,
            files_total=total_files,
            bytes_done=done_bytes,
            bytes_total=observed["file_bytes"],
            complete=True,
        )
    # Completion is published only after the frozen context's final consistency checks.
    result = {
        "schema": "encrypted-backup/v1",
        "bundle_id": manifest["bundle_id"],
        "state": "complete",
        "scope_sha256": observed["sha256"],
        "files": {
            part + ".fbenc": file_sha(destination / (part + ".fbenc"))
            for part in ("state", "vaults")
        },
        "external_secrets": "reconfigure",
        "browser_sessions": "reconfigure",
        "managed_model_credentials": "encrypted" if managed is not None else "none",
    }
    fd = os.open(
        destination / "bundle.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "wb") as handle:
        handle.write(canonical(result).encode())
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(destination)
    return result


def completion_record(bundle: Path) -> dict[str, Any]:
    """Bounded marker and file metadata only; does NOT verify ciphertext or authentication."""
    if bundle.resolve(strict=True) != bundle or not bundle.is_dir():
        raise InvalidInput("backup_bundle_path_invalid")
    marker = contained(bundle, "bundle.json")
    try:
        value = json.loads(bounded_read(marker, 4096))
        if (
            not isinstance(value, dict)
            or value.get("schema") not in {"encrypted-backup/v1", "encrypted-incremental/v2"}
            or value.get("state") != "complete"
            or not isinstance(value.get("files"), dict)
            or set(value["files"])
            != (
                {"snapshot.fbenc"}
                if value.get("schema") == "encrypted-incremental/v2"
                else {"state.fbenc", "vaults.fbenc"}
            )
            or str(UUID(value["bundle_id"])) != value["bundle_id"]
        ):
            raise ValueError()
        if (
            value["schema"] == "encrypted-incremental/v2"
            and str(UUID(value["namespace"])) != value["namespace"]
        ):
            raise ValueError()
        for sha in [value["scope_sha256"], *value["files"].values()]:
            if (
                not isinstance(sha, str)
                or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidInput("backup_marker_invalid") from exc
    for name in value["files"]:
        info = contained(bundle, name).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size == 0:
            raise InvalidInput("backup_file_type_invalid")
    return value


def layout(bundle: Path) -> dict[str, Any]:
    """Explicit full ciphertext hash verification. Password authentication occurs at restore."""
    value = completion_record(bundle)
    for name, sha in value["files"].items():
        if file_sha(contained(bundle, name)) != sha:
            raise IntegrityError("backup_ciphertext_changed")
    return value
