"""Discover only this project's dedicated archival gateways; never inspect personal Hermes."""

import hashlib
import json
import re
import shlex
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ..config import Config, InvalidInput, contained
from ..query_routing_files import read


def object_file(path: Path) -> dict[str, Any]:
    value = json.loads(read(path, 65536))
    if not isinstance(value, dict):
        raise InvalidInput("channel_legacy_configuration_invalid")
    return value


def manifest(config: Config, settings_path: Path) -> str | None:
    """Read an argument from a known launcher, never execute shell source."""
    paths = [settings_path.parent / "run-feishu.sh"]
    try:
        deployment = object_file(contained(config.state_root, "deployed-ui.json"))
        release = Path(deployment["release"])
        relative = release.relative_to(config.project_root).as_posix()
        paths.append(contained(config.project_root, relative + "/run-feishu.sh"))
    except (OSError, ValueError, KeyError, TypeError, InvalidInput):
        pass
    local = settings_path.parent / "runtime-manifest.json"
    if local.is_file() and not local.is_symlink():
        return str(local)
    for path in paths:
        try:
            for line in read(path, 65536).decode().splitlines():
                args = shlex.split(line)
                if "--settings" not in args or "--runtime-manifest" not in args:
                    continue
                if Path(args[args.index("--settings") + 1]) != settings_path:
                    continue
                value = Path(args[args.index("--runtime-manifest") + 1])
                relative = value.relative_to(config.project_root).as_posix()
                safe = contained(config.project_root, relative)
                if safe.is_file():
                    return str(safe)
        except (OSError, ValueError, IndexError, InvalidInput):
            continue
    return None


def entry(config: Config, path: Path) -> dict[str, Any]:
    raw = object_file(path)
    if (
        raw.get("version") != 1
        or type(raw.get("enabled")) is not bool
        or not isinstance(raw.get("app_id"), str)
        or not re.fullmatch(r"cli_[A-Za-z0-9]+", raw["app_id"])
        or raw.get("home") != path.parent.relative_to(config.state_root).as_posix()
        or not isinstance(raw.get("owner_id"), str)
        or not raw["owner_id"].startswith("ou_")
    ):
        raise InvalidInput("channel_legacy_configuration_invalid")
    for key in ("client_id", "vault_id"):
        if str(UUID(raw[key])) != raw[key]:
            raise InvalidInput("channel_legacy_configuration_invalid")
    for key in ("download_limit", "store_limit", "model_limit", "outbound_limit"):
        if type(raw.get(key, 0)) is not int or not 0 <= raw.get(key, 0) <= 100:
            raise InvalidInput("channel_legacy_configuration_invalid")
    relative = path.relative_to(config.state_root).as_posix()
    return {
        "id": str(uuid5(NAMESPACE_URL, "fiber-legacy-channel:" + relative)),
        "origin": "legacy",
        "revision": 1,
        "state": "running" if raw["enabled"] else "paused",
        "owner": raw["owner_id"],
        "candidate": None,
        "challenge": None,
        "value": raw,
        "settings_path": relative,
        "configuration_sha256": hashlib.sha256(read(path, 65536)).hexdigest(),
        "runtime_manifest": manifest(config, path),
    }


def entries(config: Config) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    parent = contained(config.state_root, "configs")
    values: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    if not parent.is_dir():
        return values, issues
    for folder in sorted(parent.iterdir()):
        if not folder.name.startswith("hermes-feishu-") or folder.name.startswith(
            "hermes-feishu-gui-"
        ):
            continue
        try:
            safe = contained(parent, folder.name + "/receiver.json")
            if safe.is_file():
                if len(values) >= 50:
                    issues.append({"reason": "legacy_list_limit"})
                    break
                values.append(entry(config, safe))
        except (OSError, ValueError, KeyError, TypeError, InvalidInput):
            issues.append({"reason": "legacy_configuration_unreadable"})
    return values, issues


def home(config: Config, channel: dict[str, Any]) -> Path:
    return contained(config.state_root, channel["value"]["home"])
