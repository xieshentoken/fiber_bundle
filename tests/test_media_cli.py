import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_media_jobs import POLICY
from test_media_jobs import media_scope as media_scope

import fiber_bundle
from fiber_bundle import media_jobs


def command(config, scope, tmp_path, action, *extra):
    path = tmp_path / "client-token"
    if not path.exists():
        path.write_text(scope[0]["token"])
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "fiber_bundle.media_cli",
            action,
            "--project-root",
            str(config.project_root),
            "--config",
            "config.json",
            "--token-file",
            str(path),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(fiber_bundle.__file__).resolve().parents[1])},
    )


def test_cli_create_status_cancel_and_no_content(config, media_scope, tmp_path):
    media, subtitle = media_scope[2]
    value = command(
        config,
        media_scope,
        tmp_path,
        "create",
        "--receipt-id",
        media[0],
        "--asset-id",
        media[1],
        "--subtitle-receipt-id",
        subtitle[0],
        "--subtitle-asset-id",
        subtitle[1],
        "--mode",
        "audio_only",
        "--declared-duration-ms",
        "3000",
        "--image",
        POLICY.image,
    )
    assert value.returncode == 0 and not value.stderr
    job = json.loads(value.stdout)["job_id"]
    assert "Literal" not in value.stdout and media_scope[0]["token"] not in value.stdout
    status = command(config, media_scope, tmp_path, "status", "--job-id", job)
    assert status.returncode == 0 and json.loads(status.stdout)["state"] == "queued"
    cancel = command(config, media_scope, tmp_path, "cancel", "--job-id", job)
    assert cancel.returncode == 0 and json.loads(cancel.stdout)["state"] == "cancelled"
    run = command(config, media_scope, tmp_path, "run", "--job-id", job)
    assert run.returncode == 1 and json.loads(run.stdout) == {"error": "media_request_rejected"}


@pytest.mark.parametrize("change", ["token", "query", "symlink"])
def test_cli_rejects_without_private_diagnostics(config, media_scope, tmp_path, change):
    job = media_jobs.create(
        config, media_scope[0]["id"], *media_scope[2][0][:2], "audio_only", 3000, POLICY
    )
    token = tmp_path / "client-token"
    token.write_text("invalid" if change == "token" else media_scope[0]["token"])
    if change == "symlink":
        target = tmp_path / "original-token"
        token.rename(target)
        token.symlink_to(target)
    value = command(
        config, media_scope, tmp_path, "result" if change == "query" else "status", "--job-id", job
    )
    assert value.returncode == 1 and not value.stderr
    assert json.loads(value.stdout) == {"error": "media_request_rejected"}
    assert media_scope[0]["token"] not in value.stdout
