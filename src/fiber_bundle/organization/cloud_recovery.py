"""User-requested single-file metadata/download boundary; no background hydration or scan."""

import json
import stat
import subprocess
import sys
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from .. import auth, cloud_files, note_changes, vaults
from ..config import Config, IntegrityError, InvalidInput, contained
from ..inputs import canonical
from ..objects import digest
from ..payloads import Identifier, Payload
from ..query_http import access


class FileScope(Payload):
    client_id: Identifier
    vault_id: Identifier
    path: str = Field(min_length=1, max_length=1000)
    action: Literal["inspect", "download", "recheck"] = "inspect"
    expected: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def target(config: Config, db: Any, client: str, vault: str, path: str) -> Any:
    try:
        note_changes.validate([{"identity": "path:" + path, "note_path": path, "sha256": "0" * 64}])
    except IntegrityError as error:
        raise InvalidInput("cloud_recovery_note_required") from error
    library = auth.grant(db, client, vault, "query")
    vaults.require_scope(config, library, path)  # Exclusions precede stat or native calls.
    root = vaults.checked_vault(config, library)
    selected = contained(root, path)
    if not path.lower().endswith(".md") or any(p.startswith(".") for p in path.split("/")):
        raise InvalidInput("cloud_recovery_note_required")
    info = selected.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1048576:
        raise IntegrityError("cloud_recovery_file_changed")
    binding = [
        str(root),
        root.stat().st_dev,
        root.stat().st_ino,
        path,
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    ]
    return selected, info, digest(canonical(binding).encode())


def native(path: Any, info: Any, action: str) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise InvalidInput("cloud_recovery_macos_required")
    try:
        result = subprocess.run(
            [
                str(cloud_files.checked_helper()),
                "--file-recovery",
                str(path),
                action,
                str(info.st_dev),
                str(info.st_ino),
            ],
            capture_output=True,
            timeout=6,
            check=True,
        )
        if len(result.stdout) > 4096:
            raise ValueError("oversized response")
        value = json.loads(result.stdout)
        if (
            not isinstance(value, dict)
            or value.get("schema") != "cloud-file-recovery/v1"
            or value.get("state")
            not in {
                "available",
                "not_requested",
                "requested",
                "downloading",
                "conflict",
                "error",
                "unknown",
            }
            or type(value.get("requested_now")) is not bool
        ):
            raise ValueError("invalid status")
        return {k: value[k] for k in ("state", "requested_now")}
    except subprocess.TimeoutExpired as error:
        raise IntegrityError(
            "cloud_recovery_download_unknown"
            if action == "download"
            else "cloud_recovery_status_timeout"
        ) from error
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise IntegrityError("cloud_recovery_status_unavailable") from error


def operate(config: Config, db: Any, data: FileScope) -> Any:
    path, info, token = target(config, db, data.client_id, data.vault_id, data.path)
    if data.action != "inspect":
        auth.grant(db, data.client_id, data.vault_id, "add")
        if data.expected != token:
            raise IntegrityError("cloud_recovery_file_changed")
    if data.action == "download":
        from .cloud_downloads import send

        status = native(path, info, "inspect")
        if status["state"] in {"available", "requested", "downloading", "conflict"}:
            result = status
        else:
            result = send(
                config, data.vault_id, data.path, token, lambda: native(path, info, "download")
            )
    else:
        result = native(path, info, "inspect")
    # The system may populate bytes asynchronously; don't retry a sent download.
    if data.action != "download":
        _, _, after = target(config, db, data.client_id, data.vault_id, data.path)
        if after != token:
            raise IntegrityError("cloud_recovery_file_changed")
    from .cloud_downloads import observed

    observed(config, data.vault_id, data.path, token, result["state"])
    if data.action == "recheck":
        if result["state"] != "available":
            raise IntegrityError("cloud_item_not_downloaded")
        cloud_files.require_local(path)
        from ..vault_sync.recheck import request as recheck

        root = vaults.checked_vault(config, auth.grant(db, data.client_id, data.vault_id, "query"))
        result["index_generation"] = recheck(config, data.vault_id, root, data.path)
    return {**result, "path": data.path, "expected": token}


def register(routes: APIRouter, config: Config) -> None:
    @routes.post("/cloud-file")
    def file_action(data: FileScope, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            return operate(config, db, data)

    @routes.get("/cloud-file/reveal")
    def reveal(client_id: str, vault_id: str, path: str, request: Request) -> Any:
        with access(config, request, admin=True) as (db, _):
            selected, info, token = target(config, db, client_id, vault_id, path)
            return {
                "absolute_path": str(selected),
                "expected": token,
                "device": info.st_dev,
                "inode": info.st_ino,
            }
