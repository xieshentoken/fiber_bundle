"""Compile the production Swift policy and check real scoped helper boundaries."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS metadata helper")


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    root = tmp_path_factory.mktemp("cloud-status")
    source = Path(__file__).parents[1] / "desktop/cloud-status.swift"
    policy = root / "policy.swift"
    policy.write_text(
        source.read_text().split("guard setiopolicy_np", 1)[0]
        + "\nlet a = CommandLine.arguments\n"
        + 'print(cloudDownloadState(ubiquitous: a[1] == "1", '
        + 'status: a[2] == "nil" ? nil : a[2]).rawValue)\n'
    )
    compiler = shutil.which("swiftc")
    assert compiler
    for name, path in [("policy", policy), ("helper", source)]:
        subprocess.run(
            [
                compiler,
                "-module-cache-path",
                str(root / "cache"),
                str(path),
                "-o",
                str(root / name),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    return root


@pytest.mark.parametrize(
    "ubiquitous,status,expected",
    [
        (False, "nil", "available"),
        (True, "nil", "invalid"),
        (True, "NotDownloaded", "pending"),
        (True, "Downloaded", "stale"),
        (True, "Current", "available"),
        (True, "Unknown", "stale"),
    ],
)
def test_directory_status_does_not_weaken_file_admission(native, ubiquitous, status, expected):
    value = status if status == "nil" else "NSURLUbiquitousItemDownloadingStatus" + status
    result = subprocess.run(
        [str(native / "policy"), str(int(ubiquitous)), value],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == expected


def test_helper_keeps_placeholder_symlink_and_scope_denials(native, tmp_path):
    def check(mode, path, *args):
        result = subprocess.run(
            [str(native / "helper"), mode, str(path), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return json.loads(result.stdout)

    assert check("--file", tmp_path)["available"]
    placeholder = tmp_path / "missing.icloud"
    placeholder.write_bytes(b"placeholder")
    assert check("--file", placeholder)["pending"] == 1
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    assert check("--file", link)["errors"] == 1
    assert check("--file", tmp_path / "absent")["errors"] == 1
    protected = tmp_path / "protected"
    protected.mkdir()
    assert check("--tree-excluding", tmp_path, '["protected"]')["excluded_present"] == 1


def test_missing_metadata_is_distinct_from_stale_or_conflicted_files():
    from fiber_bundle import cloud_files
    from fiber_bundle.config import IntegrityError

    value = {
        "schema": "cloud-status/v1",
        "available": False,
        "errors": 1,
        "metadata_unavailable": 1,
        "pending": 0,
        "stale": 0,
        "conflicts": 0,
        "excluded_present": 0,
    }
    with pytest.raises(IntegrityError, match="cloud_metadata_unavailable"):
        cloud_files._checked_status(json.dumps(value).encode())
    for key in ["pending", "stale", "conflicts", "excluded_present"]:
        with pytest.raises(ValueError):
            cloud_files._checked_status(json.dumps(value | {key: 1}).encode())
