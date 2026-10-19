"""HTTP lists bound metadata IO; explicit verification/restore still reject changed ciphertext."""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_api import payload
from test_session_search import owner as owner

from fiber_bundle import (
    backend_lifecycle,
    backup_bundle,
    backup_cli,
    backup_requests,
    backup_restore,
)
from fiber_bundle.api import create_app
from fiber_bundle.config import IntegrityError, InvalidInput

PASSWORD = "synthetic-backup-password-only"


def completed(config):
    intent = backup_requests.prepare(config, "create")
    backup_requests.execute(config, intent["id"], PASSWORD)
    return intent, config.project_root / "backups" / intent["id"]


def test_two_large_bundles_list_and_restore_preparation_never_hash_ciphertext(
    config, store, monkeypatch
):
    first, left = completed(config)
    _, right = completed(config)
    for root in [left, right]:
        for name in ["state.fbenc", "vaults.fbenc"]:
            with (root / name).open("ab") as stream:
                stream.truncate(32 * 1024 * 1024)

    def forbidden(path):
        raise AssertionError("ciphertext read while listing or preparing")

    monkeypatch.setattr(backup_bundle, "file_sha", forbidden)
    values = backup_requests.list_requests(config)
    assert len(values) == 2
    assert all(v["state"] == "encrypted_complete" and not v["ciphertext_verified"] for v in values)
    assert all(
        v["verification_command"].startswith("fiber-backup verify --bundle ") for v in values
    )
    assert backup_requests.prepare(config, "restore", first["id"])["state"] == "prepared"


def test_same_size_tamper_not_certified_by_list_but_verify_and_restore_refuse(
    config, store, tmp_path, monkeypatch, capsys
):
    _, bundle = completed(config)
    path = bundle / "state.fbenc"
    data = path.read_bytes()
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    value = backup_requests.list_requests(config)[0]
    assert value["state"] == "encrypted_complete" and value["ciphertext_verified"] is False
    with pytest.raises(IntegrityError, match="backup_ciphertext_changed"):
        backup_bundle.layout(bundle)
    with pytest.raises(IntegrityError, match="backup_ciphertext_changed"):
        backup_restore.restore(bundle, tmp_path / "restored", PASSWORD)
    assert not (tmp_path / "restored").exists()
    monkeypatch.setattr(sys, "argv", ["fiber-backup", "verify", "--bundle", str(bundle)])
    assert backup_cli.main() == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "backup_ciphertext_changed"


def test_explicit_verify_hashes_all_files_but_does_not_authenticate_or_ask_password(
    config, store, monkeypatch, capsys
):
    _, bundle = completed(config)
    actual = backup_bundle.file_sha
    inspected = []

    def recorded(path):
        inspected.append(path.name)
        return actual(path)

    monkeypatch.setattr(backup_bundle, "file_sha", recorded)
    monkeypatch.setattr(
        backup_cli.getpass, "getpass", lambda _: pytest.fail("verify must not request passwords")
    )
    monkeypatch.setattr(sys, "argv", ["fiber-backup", "verify", "--bundle", str(bundle)])
    assert backup_cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ciphertext_verified"
    assert result["authenticated"] is result["restorable"] is False
    assert sorted(inspected) == ["state.fbenc", "vaults.fbenc"]


@pytest.mark.parametrize("change", ["missing", "symlink", "hardlink", "fifo", "malformed", "large"])
def test_invalid_completion_metadata_is_incomplete_and_full_validation_refuses(
    config, store, tmp_path, change
):
    _, bundle = completed(config)
    path = bundle / "state.fbenc"
    if change in {"missing", "symlink", "hardlink", "fifo"}:
        path.unlink()
        if change != "missing":
            sentinel = tmp_path / "sentinel"
            sentinel.write_bytes(b"untouched")
            if change == "symlink":
                path.symlink_to(sentinel)
            elif change == "hardlink":
                os.link(sentinel, path)
            else:
                os.mkfifo(path)
    else:
        (bundle / "bundle.json").write_bytes(b"bad" if change == "malformed" else b"x" * 4097)
    assert backup_requests.list_requests(config)[0]["state"] == "incomplete"
    with pytest.raises((OSError, InvalidInput)):
        backup_bundle.layout(bundle)


def test_backup_list_allows_other_guarded_operations_without_ciphertext_io(
    config, store, http, owner, monkeypatch
):
    control = backend_lifecycle.BackendControl(config, "http://127.0.0.1:8765")
    control.record("running")
    completed(config)
    monkeypatch.setattr(backup_bundle, "file_sha", lambda _: pytest.fail("lock-held hash"))
    with TestClient(
        create_app(config, lifecycle=control),
        base_url=control.origin,
        client=("127.0.0.1", 49000),
    ) as parallel:
        parallel.cookies.update(http.cookies)
        parallel.headers.update(http.headers)
        with ThreadPoolExecutor(4) as pool:
            operations = [
                pool.submit(parallel.get, "/admin/backups"),
                pool.submit(parallel.get, "/admin/query/tasks"),
                pool.submit(
                    parallel.post,
                    "/v1/receipts",
                    headers=owner[1],
                    json=payload(owner[2]["personal"]),
                ),
                pool.submit(
                    parallel.post, "/admin/backend/stop", json={"instance_id": control.instance_id}
                ),
            ]
            responses = [operation.result(timeout=5) for operation in operations]
    assert [r.status_code for r in responses] == [200, 200, 202, 200]
    assert control.stop_event.is_set()
    assert responses[0].json()[0]["ciphertext_verified"] is False
