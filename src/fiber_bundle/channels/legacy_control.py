"""Explicit control of a known project archival process, never arbitrary PIDs or shell scripts."""

import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import feishu_runtime, query_policy_store
from ..config import Config, InvalidInput, contained
from ..runtime_environment import child_environment
from . import legacy, process_args, store


def guard(config: Config, home: str) -> None:
    identity = str(uuid5(NAMESPACE_URL, "fiber-legacy-channel:" + home + "/receiver.json"))
    with store.readonly(config) as db:
        if (
            db is not None
            and db.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_channels'").fetchone()
        ):
            row = db.execute("SELECT state FROM legacy_channels WHERE id=?", (identity,)).fetchone()
            if row and row[0] != "running":
                raise InvalidInput("channel_scope_withdrawn")


def disable(config: Config, channel: dict[str, Any], intent: dict[str, Any]) -> None:
    path = contained(config.state_root, channel["settings_path"])
    raw = legacy.object_file(path)
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    target = json.dumps({**channel["value"], "enabled": False}, separators=(",", ":")).encode()
    expected = hashlib.sha256(target).hexdigest()
    if current not in {intent["configuration_sha256"], expected, intent.get("unbound_sha256")}:
        raise InvalidInput("channel_revision_conflict")
    if raw.get("enabled"):
        query_policy_store.write_private(path, target)
    intent["disabled_sha256"] = expected if raw.get("enabled") else current


def signal_stop(config: Config, channel: dict[str, Any], instance_id: str | None) -> None:
    runtime = feishu_runtime.inspect(legacy.home(config, channel))
    if runtime["process_state"] == "stopped":
        return
    if runtime.get("instance_id") != instance_id or not runtime["heartbeat_fresh"]:
        raise InvalidInput("channel_legacy_process_unverified")
    pid = runtime.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        raise InvalidInput("channel_legacy_process_unverified")
    try:
        args = process_args.arguments(pid)
        if not matches_command(config, channel, args):
            raise InvalidInput("channel_legacy_process_unverified")
        fresh = feishu_runtime.inspect(legacy.home(config, channel))
        if fresh.get("instance_id") != instance_id or fresh.get("pid") != pid:
            raise InvalidInput("channel_legacy_process_unverified")
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except (OSError, ValueError, IndexError):
        raise InvalidInput("channel_legacy_process_unverified") from None


def matches_command(config: Config, channel: dict[str, Any], args: list[str]) -> bool:
    module = ["-m", "fiber_bundle.feishu_gateway_cli"]
    try:
        return (
            (args[1:3] == module or args[1:4] == ["-s", *module])
            and args.count("--settings") == 1
            and all(
                args.count(name) <= 1
                for name in ("--project-root", "--config", "--runtime-manifest")
            )
            and argument(args, "--settings")
            == str(contained(config.state_root, channel["settings_path"]))
            and (
                "--project-root" not in args
                or argument(args, "--project-root") == str(config.project_root)
            )
            and (
                "--config" not in args
                or argument(args, "--config").startswith(str(config.project_root) + "/")
            )
        )
    except (ValueError, IndexError):
        return False


def argument(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def start(config: Config, channel: dict[str, Any]) -> None:
    path = contained(config.state_root, channel["settings_path"])
    manifest = channel.get("runtime_manifest")
    if not manifest or not channel["owner"] or channel["owner"] == "ou_unpaired":
        raise InvalidInput("channel_legacy_start_unavailable")
    query_policy_store.write_private(
        path, json.dumps({**channel["value"], "enabled": True}).encode()
    )
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "fiber_bundle.feishu_gateway_cli",
            "--project-root",
            str(config.project_root),
            "--config",
            str(_storage(config, channel)),
            "--settings",
            str(path),
            "--runtime-manifest",
            manifest,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_environment(),
        start_new_session=True,
    )


def _storage(config: Config, channel: dict[str, Any]) -> Path:
    path = legacy.home(config, channel) / "channel-storage.json"
    query_policy_store.write_private(path, json.dumps(config.storage_configuration()).encode())
    return path
