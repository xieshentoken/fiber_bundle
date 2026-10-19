"""Read-only transport/install verification using separately trusted manifest hashes."""

import argparse
import hashlib
import json
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

MAX_JSON = 2 * 1024 * 1024
MAX_FILES = 20_000
SHA256 = re.compile(r"[0-9a-f]{64}")


class VerificationError(ValueError):
    """A named verification boundary failed; never print file content."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def canonical_directory(path: Path) -> Path:
    path = path.absolute()
    if path.resolve(strict=True) != path or not path.is_dir():
        raise VerificationError("canonical_directory_required")
    return path


def regular_file(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise VerificationError("regular_file_required")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError("duplicate_json_key")
        result[key] = value
    return result


def pinned_json(path: Path, expected: str) -> dict[str, object]:
    if SHA256.fullmatch(expected) is None:
        raise VerificationError("invalid_trusted_sha256")
    regular_file(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise VerificationError("manifest_too_large")
    if hashlib.sha256(data).hexdigest() != expected:
        raise VerificationError("manifest_hash_mismatch")
    value = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise VerificationError("manifest_object_required")
    return value


def file_map(value: object, *, artifacts: bool = False) -> dict[str, str]:
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_FILES:
        raise VerificationError("invalid_file_manifest")
    for name, expected in value.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise VerificationError("invalid_file_entry")
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or str(path) != name
            or "\\" in name
            or "\x00" in name
            or (artifacts and len(path.parts) != 1)
            or SHA256.fullmatch(expected) is None
        ):
            raise VerificationError("invalid_file_entry")
    return value


def verify_app(app: Path, expected: dict[str, str]) -> int:
    actual = {}
    for path in app.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            # Electron framework aliases are internal. Do not follow links into
            # other locations; codesign independently verifies the signed bundle.
            if not path.resolve(strict=True).is_relative_to(app):
                raise VerificationError("app_symlink_outside_bundle")
        elif stat.S_ISREG(mode):
            name = path.relative_to(app).as_posix()
            if name not in expected:
                raise VerificationError("unexpected_app_file")
            actual[name] = digest(path)
        elif not stat.S_ISDIR(mode):
            raise VerificationError("unsupported_app_entry")
    if actual != expected:
        raise VerificationError("app_files_mismatch")
    return len(actual)


def verify(release: Path, lock_sha: str, manifest_sha: str, app: Path | None) -> dict[str, object]:
    release = canonical_directory(release)
    lock = pinned_json(release / "release-lock.json", lock_sha)
    manifest = file_map(pinned_json(release / "app-files.json", manifest_sha))
    if (
        lock.get("schema") != "portable-release/v1"
        or lock.get("architecture") != "arm64"
        or type(lock.get("files")) is not int
        or lock["files"] != len(manifest)
    ):
        raise VerificationError("unsupported_release_lock")
    artifacts = file_map(lock.get("artifacts"), artifacts=True)
    for name, expected in artifacts.items():
        path = release / name
        regular_file(path)
        if digest(path) != expected:
            raise VerificationError("artifact_hash_mismatch")
    result: dict[str, object] = {
        "state": "ARTIFACTS_VERIFIED",
        "artifacts_verified": len(artifacts),
        "app_files_verified": None,
        "native_signature": "NOT RUN",
        "host": {
            "system": platform.system(),
            "version": platform.mac_ver()[0],
            "architecture": platform.machine(),
        },
        "release_qualified_host": lock.get("qualified_host"),
        "services_started": False,
        "credentials_read": False,
        "remaining_acceptance": {
            "iCloud_dual_device": "NOT RUN",
            "alternate_Mac_install_and_switch": "NOT RUN",
            "eight_inputs_and_real_channels": "NOT RUN",
        },
    }
    if app is not None:
        app = canonical_directory(app)
        result["app_files_verified"] = verify_app(app, manifest)
        result["state"] = "APP_BYTES_VERIFIED"
        if sys.platform == "darwin" and platform.machine() == "arm64":
            completed = subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                capture_output=True,
                timeout=60,
                check=False,
            )
            if completed.returncode:
                raise VerificationError("native_signature_invalid")
            result.update(state="APP_BYTES_AND_SIGNATURE_VERIFIED", native_signature="PASS")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--lock-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.release, args.lock_sha256, args.manifest_sha256, args.app)
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "state": "FAIL",
                    "reason": str(exc)
                    if isinstance(exc, VerificationError)
                    else type(exc).__name__,
                }
            )
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
