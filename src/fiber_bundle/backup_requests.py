"""Private offline intents. HTTP prepares bounded selections; only the terminal executes them."""

import hashlib
import json
import shlex
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from . import backup_bundle, backup_preflight, backup_restore, restore_guard
from .config import Config, InvalidInput, StorageError, contained, make_directory
from .inputs import canonical
from .query_policy_store import write_private
from .query_routing_files import read as bounded_read


def identity(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidInput("backup_request_id_invalid") from exc
    return value


def binding(config: Config) -> str:
    value = asdict(config)
    value["project_root"] = str(config.project_root)
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read(config: Config, request_id: str) -> dict[str, Any]:
    path = contained(config.project_root, ".backup-requests/" + identity(request_id) + ".json")
    if path.stat().st_size > 4096 or path.stat().st_mode & 0o777 != 0o600:
        raise InvalidInput("backup_request_invalid")
    try:
        value = json.loads(bounded_read(path, 4096))
    except ValueError as exc:
        raise InvalidInput("backup_request_invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "id", "action", "bundle", "config_sha256", "created_at"}
        or value["schema"] != "backup-request/v1"
        or value["id"] != request_id
        or value["action"] not in {"create", "restore"}
        or value["config_sha256"] != binding(config)
        or type(value["created_at"]) not in {int, float}
    ):
        raise InvalidInput("backup_request_configuration_changed")
    if value["action"] == "restore":
        identity(value["bundle"])
    elif value["bundle"] is not None:
        raise InvalidInput("backup_request_invalid")
    return value


def describe(config: Config, value: dict[str, Any]) -> dict[str, Any]:
    kind = "backups" if value["action"] == "create" else "restores"
    target = contained(config.project_root, kind + "/" + value["id"])
    status = "prepared"
    if target.exists():
        status = "incomplete"
        if value["action"] == "create":
            try:
                backup_bundle.completion_record(target)
                status = "encrypted_complete"
            except (OSError, ValueError, StorageError):
                pass
        else:
            gate = restore_guard.read(contained(target, config.state_relative))
            if gate:
                status = gate["state"]
    return {
        "id": value["id"],
        "action": value["action"],
        "state": status,
        "destination": str(target),
        "command": "fiber-backup execute --project-root "
        + shlex.quote(str(config.project_root))
        + " --request "
        + value["id"],
        "terminal_password_required": True,
        "services_started": False,
        "ciphertext_verified": False,
        "verification_command": "fiber-backup verify --bundle " + shlex.quote(str(target))
        if value["action"] == "create" and status == "encrypted_complete"
        else None,
    }


def prepare(
    config: Config, action: Literal["create", "restore"], bundle: str | None = None
) -> dict[str, Any]:
    if action not in {"create", "restore"} or (action == "create" and bundle is not None):
        raise InvalidInput("backup_request_invalid")
    # Reject known scope blockers before persisting an otherwise unusable intent.
    # This is not offline preflight; terminal execution must revalidate everything.
    backup_preflight.check_state_scope(config)
    if action == "restore":
        if bundle is None:
            raise InvalidInput("backup_request_bundle_required")
        origin = read(config, identity(bundle))
        if origin["action"] != "create":
            raise InvalidInput("backup_request_bundle_invalid")
        # A restore selection is only an intent. The terminal authenticates it before writing.
        backup_bundle.completion_record(contained(config.project_root, "backups/" + bundle))
    request_root = contained(config.project_root, ".backup-requests")
    make_directory(request_root)
    if len(_request_paths(request_root)) >= 50:
        raise InvalidInput("backup_request_limit")
    value: dict[str, Any] = {
        "schema": "backup-request/v1",
        "id": str(uuid4()),
        "action": action,
        "bundle": bundle,
        "config_sha256": binding(config),
        "created_at": time.time(),
    }
    write_private(contained(request_root, value["id"] + ".json"), canonical(value).encode())
    return describe(config, value)


def _request_paths(root: Path) -> list[Path]:
    paths = []
    for path in root.iterdir():
        paths.append(path)
        if len(paths) > 50:
            raise InvalidInput("backup_request_limit")
    return sorted(paths)


def list_requests(config: Config) -> list[dict[str, Any]]:
    root = contained(config.project_root, ".backup-requests")
    if not root.exists():
        return []
    paths = _request_paths(root)
    return [describe(config, read(config, p.stem)) for p in paths]


def execute(config: Config, request_id: str, password: str) -> dict[str, Any]:
    value = read(config, request_id)
    kind = "backups" if value["action"] == "create" else "restores"
    parent = contained(config.project_root, kind)
    make_directory(parent)
    target = contained(parent, request_id)
    if value["action"] == "create":
        return backup_bundle.create(config, target, password)
    # A restore intent also requires every native owner of the source project offline.
    from .backup_preflight import frozen

    with frozen(config):
        return backup_restore.restore(
            contained(config.project_root, "backups/" + value["bundle"]), target, password
        )
