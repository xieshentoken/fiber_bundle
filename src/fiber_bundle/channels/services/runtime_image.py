"""Immutable worker images; a GUI/application update cannot replace live worker imports."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...config import Config, IntegrityError, make_directory
from ...runtime_environment import child_environment


def root(config: Config, *, create: bool = False) -> Path:
    value = config.project_root / ".fiber-services"
    if create:
        make_directory(value)
    return value


def package_hash(package: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            value.update(str(path.relative_to(package)).encode())
            value.update(path.read_bytes())
    return value.hexdigest()


def pin(config: Config, *, package: Path | None = None) -> dict[str, Any]:
    package = package or Path(__file__).resolve().parents[2]
    key = package_hash(package)
    images = root(config, create=True) / "images"
    make_directory(images)
    image = images / key
    if not image.exists():
        temp = images / (".preparing-" + str(uuid4()))
        make_directory(temp)
        try:
            backend = package.parent.parent
            if package.parent.name == "site-packages" and backend.name == "backend":
                target = temp / "resources"
                if sys.platform == "darwin":
                    subprocess.run(["/bin/cp", "-cR", str(backend.parent), str(target)], check=True)
                else:
                    shutil.copytree(backend.parent, target, symlinks=True)
                site = "resources/backend/site-packages"
                python = "resources/backend/python/bin/python3.12"
                home = "resources/backend/python"
            else:
                # Source/synthetic environments pin application code and retain the venv runtime.
                shutil.copytree(
                    package,
                    temp / "site/fiber_bundle",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                site, python, home = "site", sys.executable, None
            for cache in list(temp.rglob("__pycache__")):
                shutil.rmtree(cache)
            manifest = {"id": key, "site": site, "python": python, "home": home}
            (temp / "image.json").write_text(json.dumps(manifest))
            assert package_hash(temp / site / "fiber_bundle") == key
            try:
                os.rename(temp, image)
            except OSError:
                if not image.exists():
                    raise
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    value = json.loads((image / "image.json").read_text())
    if value["id"] != key or package_hash(image / value["site"] / "fiber_bundle") != key:
        raise IntegrityError("channel_service_image_changed")
    return {**value, "root": str(image)}


def environment(image: dict[str, Any]) -> dict[str, str]:
    env = child_environment()
    base = Path(image["root"])
    env["PYTHONPATH"] = str(base / image["site"])
    if image["home"]:
        env["PYTHONHOME"] = str(base / image["home"])
        for name, relative in [
            ("FIBER_CLOUD_HELPER", "cloud-status"),
            ("FIBER_TRASH_HELPER", "vault-trash"),
        ]:
            env[name] = str(base / "resources/backend/tools" / relative)
    return env


def resolve(config: Config, identity: str) -> dict[str, Any]:
    if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise IntegrityError("channel_service_image_invalid")
    image = root(config) / "images" / identity
    if image.is_symlink():
        raise IntegrityError("channel_service_image_invalid")
    value = json.loads((image / "image.json").read_text())
    if value["id"] != identity or package_hash(image / value["site"] / "fiber_bundle") != identity:
        raise IntegrityError("channel_service_image_changed")
    return {**value, "root": str(image)}
