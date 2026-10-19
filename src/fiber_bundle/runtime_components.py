"""Explicit local component installation with a separately supplied manifest pin."""

import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Config, InvalidInput, contained, make_directory
from .query_policy_store import write_private
from .query_routing_files import read


def verify(root: Path, manifest: Path, pin: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", pin) or root.resolve() != root:
        raise InvalidInput("component_path_or_pin_invalid")
    raw = read(manifest, 4 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != pin:
        raise InvalidInput("component_manifest_changed")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "id", "platform", "files", "runtime"}
        or value["schema"] != "fiber-component/v1"
        or value["platform"] != "darwin-arm64-cp312"
        or value["id"] not in {"hermes-feishu", "hermes-agent"}
        or not isinstance(value["files"], dict)
        or not 1 <= len(value["files"]) <= 20000
    ):
        raise InvalidInput("component_manifest_invalid")
    runtime = value["runtime"]
    if (
        not isinstance(runtime, dict)
        or set(runtime) != {"source", "dependencies", "scanner"}
        or not isinstance(runtime["dependencies"], list)
        or not 1 <= len(runtime["dependencies"]) <= 3
    ):
        raise InvalidInput("component_runtime_invalid")
    for name in [
        runtime["source"],
        *runtime["dependencies"],
        *([runtime["scanner"]] if "scanner" in runtime else []),
    ]:
        contained(root, name)
    expected = value["files"]
    actual = {}
    for path in root.rglob("*"):
        if path == manifest:
            continue
        if path.is_symlink():
            raise InvalidInput("component_symlink_forbidden")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, digest in expected.items():
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or str(relative) != name
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise InvalidInput("component_entry_invalid")
    if actual != expected:
        raise InvalidInput("component_files_changed")
    return value


def install(config: Config, bundle: Path, pin: str) -> dict[str, Any]:
    bundle = bundle.absolute()
    value = verify(bundle, bundle / "component.json", pin)
    config.validate()
    output = (
        config.state_root / "configs/hermes-installation.json"
        if value["id"] == "hermes-agent"
        else config.state_root / "runtime/hermes-feishu-runtime.json"
    )
    if output.exists():
        raise InvalidInput("component_existing_configuration_preserved")
    parent = contained(config.state_root, "runtime")
    make_directory(parent)
    target = contained(parent, value["id"])
    if target.exists():
        raise InvalidInput("component_fresh_destination_required")
    staging = contained(parent, value["id"] + ".installing")
    if staging.exists():
        raise InvalidInput("component_incomplete_install_requires_review")
    shutil.copytree(bundle, staging)
    verify(staging, staging / "component.json", pin)
    staging.rename(target)
    runtime = value["runtime"]
    if value["id"] == "hermes-feishu":
        source = contained(target, runtime["source"])
        scanner = contained(target, runtime["scanner"])
        manifest = {
            "component": {"root": str(target.relative_to(config.project_root)), "sha256": pin},
            "source": str(source.relative_to(config.project_root)),
            "dependencies": [
                str(contained(target, p).relative_to(config.project_root))
                for p in runtime["dependencies"]
            ],
            "sha256": {
                n.removeprefix(runtime["source"] + "/"): h
                for n, h in value["files"].items()
                if n
                in {
                    runtime["source"] + "/" + part
                    for part in (
                        "hermes_constants.py",
                        "gateway/config.py",
                        "gateway/run.py",
                        "gateway/status.py",
                        "gateway/platforms/base.py",
                        "plugins/platforms/feishu/adapter.py",
                        "plugins/platforms/feishu/plugin.yaml",
                    )
                }
            },
            "scanner": str(scanner.relative_to(config.project_root)),
            "scanner_sha256": value["files"][runtime["scanner"]],
        }
        output = parent / "hermes-feishu-runtime.json"
    else:
        import sys

        manifest = {
            "schema": "fiber-hermes-installation/v1",
            "root": str(contained(target, runtime["source"])),
            "python": sys.executable,
            "dependency_root": str(contained(target, runtime["dependencies"][0])),
            "component_root": str(target),
            "component_sha256": pin,
            "scanner": str(contained(target, runtime["scanner"])),
        }
        output = config.state_root / "configs/hermes-installation.json"
        make_directory(output.parent)
    write_private(output, json.dumps(manifest, sort_keys=True).encode())
    return {
        "state": "installed",
        "component": value["id"],
        "manifest": str(output),
        "services_started": False,
    }
