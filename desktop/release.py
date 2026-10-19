"""Build a fresh arm64 DMG with a relocatable, independently invoked backend runtime."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def dependencies(target: Path) -> dict[str, str]:
    pending = ["fastapi", "uvicorn", "aiohttp", "cryptography", "mcp"]
    copied: dict[str, str] = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in copied:
            continue
        dist = importlib.metadata.distribution(name)
        copied[name] = dist.version
        for requirement in dist.requires or ():
            value = Requirement(requirement)
            if value.marker is None or value.marker.evaluate({"extra": ""}):
                pending.append(value.name)
        for relative in dist.files or ():
            parts = relative.parts
            if (
                ".." in parts
                or "__pycache__" in parts
                or relative.suffix == ".pth"
                or relative.name == "direct_url.json"
            ):
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file() or source.is_symlink():
                raise ValueError("dependency contains unsupported entry")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return dict(sorted(copied.items()))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--wheel", type=Path, required=True)
    p.add_argument("--python-runtime", type=Path, required=True)
    args = p.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("only macOS arm64 qualified")
    out = args.out.absolute()
    if out.exists() or out.is_symlink() or out.parent.resolve(strict=True) != out.parent:
        raise ValueError("fresh canonical output required")
    root = Path(__file__).resolve().parent.parent
    runtime = args.python_runtime.resolve(strict=True)
    wheel = args.wheel.resolve(strict=True)
    if not (runtime / "bin/python3.12").is_file() or not zipfile.is_zipfile(wheel):
        raise ValueError("explicit standalone runtime and wheel required")
    actual = subprocess.check_output(
        [
            str(runtime / "bin/python3.12"),
            "-I",
            "-c",
            "import platform; print(platform.python_version(), platform.machine())",
        ],
        text=True,
    ).strip()
    if actual != "3.12.13 arm64" or (runtime / "BUILD").read_text().strip() != "20260807":
        raise ValueError("runtime version, architecture or build differs")
    out.mkdir(mode=0o700)
    run("node", str(root / "desktop/build.mjs"), "--out", str(out / "shell"))
    payload = out / "payload"
    payload.mkdir(mode=0o700)
    app = payload / "Fiber bundle.app"
    built = out / "shell/Fiber bundle-darwin-arm64/Fiber bundle.app"
    shutil.move(str(built), str(app))
    resources = app / "Contents/Resources"
    backend = resources / "backend"
    backend.mkdir()
    notices = root / "desktop/runtime-licenses"
    sources = json.loads((notices / "sources.json").read_text())
    for name, value in sources["files"].items():
        if Path(name).name != name or digest(notices / name) != value["sha256"]:
            raise ValueError("runtime license snapshot changed")
    shutil.copytree(notices, backend / "licenses")
    python = backend / "python"
    shutil.copytree(
        runtime,
        python,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages", "BUILD"),
    )
    # Remove build/install scripts with build-machine shebangs; retain runtime licenses.
    for path in (python / "bin").iterdir():
        if path.name != "python3.12":
            path.unlink()
    site = backend / "site-packages"
    site.mkdir()
    versions = dependencies(site)
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or ".data" in relative.parts:
                raise ValueError("invalid wheel layout")
        archive.extractall(site)
    tools = backend / "tools"
    tools.mkdir()
    helper = tools / "cloud-status"
    run(
        "/usr/bin/swiftc",
        "-module-cache-path",
        str(out / "swift-cache"),
        str(root / "desktop/cloud-status.swift"),
        "-o",
        str(helper),
    )
    run("/usr/bin/codesign", "--force", "--sign", "-", str(helper))
    trash_helper = tools / "vault-trash"
    run(
        "/usr/bin/swiftc",
        "-module-cache-path",
        str(out / "swift-cache"),
        str(root / "desktop/vault-trash.swift"),
        "-o",
        str(trash_helper),
    )
    run("/usr/bin/codesign", "--force", "--sign", "-", str(trash_helper))
    trash_pin = digest(trash_helper)
    pin = digest(helper)
    launchers = resources / "bin"
    launchers.mkdir()
    modules = {
        "fiber-bundle": "cli",
        "fiber-backend": "backend_cli",
        "fiber-backup": "backup_cli",
        "fiber-setup": "setup_cli",
        "fiber-weixin-worker": "weixin_worker_cli",
        "fiber-execute": "execution_cli",
        "fiber-publish": "publication_cli",
        "fiber-answer-worker": "answer_worker_cli",
        "fiber-document-worker": "document_creation_worker_cli",
        "fiber-notes": "note_changes_cli",
        "fiber-note-migrate": "note_migration_cli",
        "fiber-moc": "moc_cli",
        "fiber-moc-dispatch": "moc_dispatch_cli",
        "fiber-agent": "agent_connections.cli",
    }
    for name, module in modules.items():
        path = launchers / name
        path.write_text(
            "#!/bin/sh\nset -eu\n"
            'FIBER_RESOURCE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)\n'
            'export PYTHONHOME="$FIBER_RESOURCE_ROOT/backend/python"\n'
            'export PYTHONPATH="$FIBER_RESOURCE_ROOT/backend/site-packages"\n'
            "export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1\n"
            'export FIBER_CLOUD_HELPER="$FIBER_RESOURCE_ROOT/backend/tools/cloud-status"\n'
            f"export FIBER_CLOUD_HELPER_SHA256={pin}\n"
            'export FIBER_TRASH_HELPER="$FIBER_RESOURCE_ROOT/backend/tools/vault-trash"\n'
            f"export FIBER_TRASH_HELPER_SHA256={trash_pin}\n"
            f'exec "$PYTHONHOME/bin/python3.12" -s -m fiber_bundle.{module} "$@"\n'
        )
        path.chmod(0o755)
    shutil.copy2(root / "README.md", resources / "使用说明.md")
    lock = {
        "schema": "portable-release/v1",
        "architecture": "arm64",
        "python": "3.12.13",
        "electron": json.loads((root / "node_modules/electron/package.json").read_text())[
            "version"
        ],
        "wheel_sha256": digest(wheel),
        "dependencies": versions,
        "cloud_helper_sha256": pin,
        "trash_helper_sha256": trash_pin,
        "developer_id_signed": False,
        "notarized": False,
        "signing": "ad-hoc",
        "services_started": False,
    }
    (resources / "release-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    for path in backend.rglob("*"):
        if path.is_symlink() and (
            not path.resolve().is_relative_to(python) or os.readlink(path).startswith("/")
        ):
            raise ValueError("non-relocatable runtime link")
    # Re-seal relocated nested framework resources, preserving Electron JIT entitlements.
    run(
        "/usr/bin/codesign",
        "--force",
        "--deep",
        "--sign",
        "-",
        "--preserve-metadata=entitlements",
        str(app),
    )
    current_pin = digest(helper)
    if current_pin != pin:
        for path in launchers.iterdir():
            path.write_text(path.read_text().replace(pin, current_pin))
        lock["cloud_helper_sha256"] = current_pin
        (resources / "release-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        run(
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--preserve-metadata=entitlements",
            str(app),
        )
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    manifest = {
        str(p.relative_to(app)): digest(p)
        for p in app.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    (out / "app-files.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    (payload / "Applications").symlink_to("/Applications", target_is_directory=True)
    dmg = out / "Fiber-bundle-macOS-arm64.dmg"
    run(
        "/usr/bin/hdiutil",
        "create",
        "-quiet",
        "-format",
        "UDZO",
        "-volname",
        "Fiber bundle",
        "-srcfolder",
        str(payload),
        str(dmg),
    )
    lock.update(dmg_sha256=digest(dmg), app=str(app), dmg=str(dmg), files=len(manifest))
    (out / "release-result.json").write_text(json.dumps(lock, indent=2) + "\n")
    print(json.dumps(lock))


if __name__ == "__main__":
    main()
