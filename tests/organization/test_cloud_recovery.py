"""Scoped metadata/download requests, no original body reads or implicit retries."""

import json
import subprocess
from unittest.mock import Mock

import pytest
from test_cloud_directory_status import native as native
from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import cloud_recovery


def test_download_needs_current_metadata_token_and_single_explicit_call(config, ready, monkeypatch):
    cid, vid, root, _, note = ready
    native_call = Mock(return_value={"state": "not_requested", "requested_now": False})
    monkeypatch.setattr(cloud_recovery, "native", native_call)
    scope = dict(client_id=cid, vault_id=vid, path="Inbox/传热.md")
    with database.connect(config.state_root) as db:
        checked = cloud_recovery.operate(config, db, cloud_recovery.FileScope(**scope))
        assert native_call.call_args.args[-1] == "inspect"
        with pytest.raises(IntegrityError):
            cloud_recovery.operate(config, db, cloud_recovery.FileScope(**scope, action="download"))
        assert native_call.call_count == 1
        native_call.side_effect = [
            {"state": "not_requested", "requested_now": False},
            {"state": "requested", "requested_now": True},
        ]
        sent = cloud_recovery.operate(
            config,
            db,
            cloud_recovery.FileScope(**scope, action="download", expected=checked["expected"]),
        )
        assert sent["requested_now"] and native_call.call_count == 3
        assert native_call.call_args.args[-1] == "download"
        note.write_text(note.read_text() + "\nUser correction\n")
        with pytest.raises(IntegrityError):
            cloud_recovery.operate(
                config,
                db,
                cloud_recovery.FileScope(**scope, action="download", expected=checked["expected"]),
            )
        assert native_call.call_count == 3


def test_excluded_path_never_reaches_native_and_errors_are_actionable(
    http, config, ready, monkeypatch
):
    cid, vid, root, *_ = ready
    native_call = Mock(side_effect=IntegrityError("cloud_recovery_status_timeout"))
    monkeypatch.setattr(cloud_recovery, "native", native_call)
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["Inbox/private"]}), vid),
        )
    p = root / "Inbox/private"
    p.mkdir()
    (p / "secret.md").write_text("secret")
    for path in ("Inbox/private/secret.md", "../outside.md"):
        r = http.post(
            "/admin/organization/cloud-file", json={"client_id": cid, "vault_id": vid, "path": path}
        )
        assert r.status_code in {404, 422, 409}
    assert native_call.call_count == 0
    r = http.post(
        "/admin/organization/cloud-file",
        json={"client_id": cid, "vault_id": vid, "path": "Inbox/传热.md"},
    )
    assert r.status_code == 409 and r.json()["error"] == "cloud_recovery_status_timeout"
    assert native_call.call_count == 1


def test_timeout_does_not_retry_uncertain_download(config, ready, monkeypatch):
    cid, vid, root, *_ = ready
    path = root / "Inbox/传热.md"
    monkeypatch.setattr(cloud_recovery.cloud_files, "checked_helper", lambda: root / "fake-helper")
    call = Mock(side_effect=subprocess.TimeoutExpired("native", 6))
    monkeypatch.setattr(cloud_recovery.subprocess, "run", call)
    with pytest.raises(IntegrityError, match="download_unknown"):
        cloud_recovery.native(path, path.lstat(), "download")
    assert call.call_count == 1


def test_real_helper_regular_file_is_ready_and_never_downloads(native, tmp_path):
    path = tmp_path / "ordinary.md"
    path.write_text("# Current local note\n")
    info = path.lstat()
    for action in ("inspect", "download"):
        r = subprocess.run(
            [
                str(native / "helper"),
                "--file-recovery",
                str(path),
                action,
                str(info.st_dev),
                str(info.st_ino),
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        value = json.loads(r.stdout)
        assert value["state"] == "available" and not value["requested_now"]
        assert path.read_text() == "# Current local note\n"
    rejected = subprocess.run(
        [
            str(native / "helper"),
            "--file-recovery",
            str(path),
            "download",
            str(info.st_dev),
            str(info.st_ino + 1),
        ],
        capture_output=True,
        timeout=10,
    )
    assert rejected.returncode != 0
