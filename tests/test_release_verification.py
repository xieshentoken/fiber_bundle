"""Transport and installed bundle checks do not authorize project operations."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "desktop/verify_release.py"
spec = importlib.util.spec_from_file_location("verify_release", SCRIPT)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "release"
    root.mkdir()
    app = tmp_path / "Synthetic.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/file").write_bytes(b"synthetic application")
    (root / "candidate.dmg").write_bytes(b"synthetic transport")
    manifest = {"Contents/file": sha(b"synthetic application")}
    lock = {
        "schema": "portable-release/v1",
        "architecture": "arm64",
        "files": 1,
        "artifacts": {"candidate.dmg": sha(b"synthetic transport")},
        "qualified_host": "synthetic fixture only",
    }
    (root / "app-files.json").write_text(json.dumps(manifest))
    (root / "release-lock.json").write_text(json.dumps(lock))
    return root, app


def pins(root):
    return (verifier.digest(root / "release-lock.json"), verifier.digest(root / "app-files.json"))


def test_cli_read_only_transport_check(release):
    root, app = release
    lock, manifest = pins(root)
    before = {p: p.read_bytes() for base in (root, app) for p in base.rglob("*") if p.is_file()}
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--release",
            str(root),
            "--lock-sha256",
            lock,
            "--manifest-sha256",
            manifest,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["state"] == "ARTIFACTS_VERIFIED"
    assert data["native_signature"] == "NOT RUN"
    assert not data["services_started"] and not data["credentials_read"]
    assert set(data["remaining_acceptance"].values()) == {"NOT RUN"}
    assert before == {
        p: p.read_bytes() for base in (root, app) for p in base.rglob("*") if p.is_file()
    }


def test_app_bytes_without_native_qualification(release, monkeypatch):
    root, app = release
    monkeypatch.setattr(verifier.sys, "platform", "linux")
    result = verifier.verify(root, *pins(root), app)
    assert result["state"] == "APP_BYTES_VERIFIED"
    assert result["app_files_verified"] == 1
    assert result["native_signature"] == "NOT RUN"


@pytest.mark.parametrize("target", ["release-lock.json", "app-files.json"])
def test_tampered_manifest_rejected_before_app_execution(release, target):
    root, app = release
    original = pins(root)
    (root / target).write_text("{}")
    with pytest.raises(verifier.VerificationError, match="manifest_hash_mismatch"):
        verifier.verify(root, *original, app)


def test_tampered_transport_rejected(release):
    root, app = release
    (root / "candidate.dmg").write_bytes(b"changed")
    with pytest.raises(verifier.VerificationError, match="artifact_hash_mismatch"):
        verifier.verify(root, *pins(root), app)


@pytest.mark.parametrize("change", ["missing", "changed", "extra"])
def test_app_inventory_requires_exact_match(release, change):
    root, app = release
    if change == "missing":
        (app / "Contents/file").unlink()
    elif change == "changed":
        (app / "Contents/file").write_bytes(b"changed")
    else:
        (app / "Contents/extra").write_bytes(b"extra")
    with pytest.raises(verifier.VerificationError, match="app_files_mismatch|unexpected_app_file"):
        verifier.verify(root, *pins(root), app)


def test_app_symlink_escape_rejected(release, tmp_path):
    root, app = release
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (app / "Contents/alias").symlink_to(outside)
    with pytest.raises(verifier.VerificationError, match="app_symlink_outside_bundle"):
        verifier.verify(root, *pins(root), app)


def test_artifact_symlink_rejected(release):
    root, app = release
    (root / "candidate.dmg").rename(root / "elsewhere")
    (root / "candidate.dmg").symlink_to(root / "elsewhere")
    with pytest.raises(verifier.VerificationError, match="regular_file_required"):
        verifier.verify(root, *pins(root), app)


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/outside",
        "Contents/../outside",
        "./file",
        "Contents//file",
        "Contents\\file",
        "",
    ],
)
def test_manifest_path_traversal_rejected(name):
    with pytest.raises(verifier.VerificationError, match="invalid_file_entry"):
        verifier.file_map({name: sha(b"value")})


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_bytes(b'{"files":1,"files":2}')
    with pytest.raises(verifier.VerificationError, match="duplicate_json_key"):
        verifier.pinned_json(path, verifier.digest(path))


def test_manifest_limit(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (verifier.MAX_JSON + 1))
    with pytest.raises(verifier.VerificationError, match="manifest_too_large"):
        verifier.pinned_json(path, verifier.digest(path))


def test_signature_failure_is_cli_failure(release, monkeypatch, capsys):
    root, app = release
    lock, manifest = pins(root)
    monkeypatch.setattr(verifier.sys, "platform", "darwin")
    monkeypatch.setattr(verifier.platform, "machine", lambda: "arm64")
    calls = []

    def codesign(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, stderr=b"synthetic signature invalid")

    monkeypatch.setattr(verifier.subprocess, "run", codesign)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--release",
            str(root),
            "--app",
            str(app),
            "--lock-sha256",
            lock,
            "--manifest-sha256",
            manifest,
        ],
    )
    assert verifier.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "state": "FAIL",
        "reason": "native_signature_invalid",
    }
    assert calls[0][0] == ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
    assert calls[0][1]["timeout"] == 60


def test_pinned_invalid_shape_is_failure(release):
    root, app = release
    lock = json.loads((root / "release-lock.json").read_text())
    lock["files"] = True
    (root / "release-lock.json").write_text(json.dumps(lock))
    with pytest.raises(verifier.VerificationError, match="unsupported_release_lock"):
        verifier.verify(root, *pins(root), app)
