"""Inspect installed prerequisites or explicitly initialize a fresh local project."""

import argparse
import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path

from . import build_identity, cloud_files, database
from .backup_bundle import fresh_path
from .config import Config, StorageError
from .inputs import canonical
from .query_policy_store import write_private

VAULTS = {"personal": "test vault/temp_vault1", "supplier": "test vault/temp_vault2"}


def inspect() -> dict[str, object]:
    versions = {}
    for name in ("fiber-bundle", "fastapi", "uvicorn", "aiohttp", "cryptography"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return {
        "state": "ready" if "missing" not in versions.values() else "dependencies_missing",
        "python": platform.python_version(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "components": versions,
        "build": build_identity.inspect(),
        "docker_on_path": shutil.which("docker") is not None,
        "external_components": (
            "Docker, Hermes/Pi, models, ASR and Weixin require explicit configuration"
        ),
        "services_started": False,
        "credentials_read": False,
    }


def initialize(root: Path, overrides: dict[str, str]) -> None:
    root = fresh_path(root)
    # Validate selected existing external roots before creating the project.
    for path in overrides.values():
        cloud_files.require_available(Path(path), tree=True)
    root.mkdir(mode=0o700)
    config = Config(
        root, ".local-state", tuple(sorted(VAULTS.items())), tuple(sorted(overrides.items()))
    )
    config.validate()
    write_private(
        root / "config.test.json",
        canonical(
            {
                "state_root": config.state_relative,
                "vaults": VAULTS,
                **({"vault_roots": overrides} if overrides else {}),
            }
        ).encode(),
    )
    database.initialize(config)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("check", "init"))
    p.add_argument("--project-root", type=Path)
    p.add_argument("--personal-vault", type=Path)
    p.add_argument("--supplier-vault", type=Path)
    args = p.parse_args()
    if args.command == "check" and any(
        (args.project_root, args.personal_vault, args.supplier_vault)
    ):
        p.error("check does not accept project or vault destinations")
    try:
        result = inspect()
        if args.command == "init":
            if args.project_root is None or result["state"] != "ready":
                raise StorageError("fresh project and installed dependencies required")
            overrides = {
                name: str(path)
                for name, path in (
                    ("personal", args.personal_vault),
                    ("supplier", args.supplier_vault),
                )
                if path is not None
            }
            initialize(args.project_root, overrides)
            result.update(
                state="initialized", schema_version=database.SCHEMA_VERSION, password_set=False
            )
        print(json.dumps(result))
        return 0 if result["state"] in {"ready", "initialized"} else 1
    except (OSError, ValueError, StorageError) as exc:
        print(json.dumps({"state": "error", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
