import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_media_glossary import RAW
from test_media_jobs import create
from test_media_jobs import media_scope as media_scope

import fiber_bundle
from fiber_bundle import media_jobs
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_glossary_cli import read


def call(config, scope, tmp_path, job, action, *extra):
    path = tmp_path / "client-token"
    if not path.exists():
        path.write_text(scope[0]["token"])
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "fiber_bundle.media_glossary_cli",
            action,
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
            "--token-file",
            str(path),
            "--job-id",
            job,
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(fiber_bundle.__file__).resolve().parents[1])},
    )


def test_cli_explicit_revision_and_hash_confirm_without_automatic_approval(
    config, media_scope, tmp_path
):
    job = create(config, media_scope)
    file = tmp_path / "glossary.json"
    file.write_bytes(RAW)
    frozen = call(config, media_scope, tmp_path, job, "freeze", "--glossary-file", str(file))
    assert frozen.returncode == 0 and not frozen.stderr
    value = json.loads(frozen.stdout)
    assert not value["confirmation_recorded"] and "Synthetic terminology" not in frozen.stdout
    status = call(config, media_scope, tmp_path, job, "status")
    assert status.returncode == 0 and json.loads(status.stdout) == value
    denied = call(config, media_scope, tmp_path, job, "show")
    assert denied.returncode == 1 and json.loads(denied.stdout) == {
        "error": "media_glossary_request_rejected"
    }
    stale = call(
        config, media_scope, tmp_path, job, "confirm", "--revision", "1", "--sha256", "0" * 64
    )
    assert stale.returncode == 1 and not stale.stderr
    approved = call(
        config,
        media_scope,
        tmp_path,
        job,
        "confirm",
        "--revision",
        "1",
        "--sha256",
        value["snapshot_sha256"],
    )
    assert approved.returncode == 0 and json.loads(approved.stdout)["terminology_gate_passed"]
    assert not json.loads(approved.stdout)["asr_ready"]
    media_jobs.stop(config, media_scope[0]["id"], job)
    rejected = call(
        config,
        media_scope,
        tmp_path,
        job,
        "confirm",
        "--revision",
        "1",
        "--sha256",
        value["snapshot_sha256"],
    )
    assert rejected.returncode == 1 and not rejected.stderr
    assert media_scope[0]["token"] not in rejected.stdout


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "oversize", "empty"])
def test_input_file_is_bounded_nonredirected_regular(tmp_path, kind):
    original = tmp_path / "original"
    original.write_bytes(RAW)
    path = tmp_path / "glossary.json"
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b" " * 65537 if kind == "oversize" else b"")
    with pytest.raises((OSError, InvalidInput)):
        read(path)
    assert original.read_bytes() == RAW


def test_source_glossary_file_is_never_modified(tmp_path):
    path = tmp_path / "glossary.json"
    path.write_bytes(RAW)
    assert read(path) == RAW and path.read_bytes() == RAW
