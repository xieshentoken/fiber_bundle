"""Actual resume failures retain precise bounded evidence without rewriting the task."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_jobs import ready as ready
from test_maintenance_finalization import shared

from fiber_bundle import auth, database, moc_placement_projection, note_placement_execution
from fiber_bundle.api import create_app
from fiber_bundle.organization import maintenance, store


@pytest.mark.parametrize("change,field", [("edit", "sha256"), ("replace", "inode")])
def test_resume_rejects_real_committed_file_change_with_http_diagnostic(
    config, ready, change, field
):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        job = shared(config, db, ready)
        with patch.object(moc_placement_projection, "finalize", side_effect=OSError("crash")):
            maintenance.run(config, db, job)
    if change == "edit":
        moc.write_text(moc.read_text() + "\nManual edit must be preserved\n")
    else:
        replacement = root / ".external-replacement"
        replacement.write_bytes(moc.read_bytes())
        replacement.replace(moc)
    before = deepcopy(store.get(config, "jobs", job["id"]))
    files = {path: (path.read_bytes(), path.stat().st_ino) for path in root.rglob("*.md")}
    auth.set_password(config, "synthetic-diagnostic-password")
    # Start the HTTP app only after constructing the interrupted task. Otherwise
    # its real worker races the test's deliberate crash injection.
    with (
        TestClient(
            create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49000)
        ) as http,
        patch.object(note_placement_execution, "move", side_effect=AssertionError("replay")),
        patch.object(note_placement_execution, "exchange", side_effect=AssertionError("replay")),
        patch.object(note_placement_execution, "complete", side_effect=AssertionError("replay")),
    ):
        login = http.post(
            "/admin/login",
            json={"password": "synthetic-diagnostic-password"},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert login.status_code == 200, login.text
        http.headers.update(
            {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": login.json()["csrf"]}
        )
        response = http.post(
            f"/admin/organization/maintenance/{job['id']}/resume",
            json={"client_id": cid, "vault_id": vid},
        )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "placement_note_changed"
    assert response.json()["error_detail"] == {
        "reason": "committed_file_changed",
        "phase": "archive_committed_verify",
        "path": "Map/热管理 MOC.md",
        "changed_fields": [field],
        "retryable_read": False,
    }
    assert store.get(config, "jobs", job["id"]) == before
    assert all((path.read_bytes(), path.stat().st_ino) == old for path, old in files.items())
