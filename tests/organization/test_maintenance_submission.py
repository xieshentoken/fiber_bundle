"""A slow POST prepares read-only, then reauthorizes and deduplicates under the writer."""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from test_jobs import ready as ready

from fiber_bundle import auth, database
from fiber_bundle.organization import maintenance_submission, store


def test_writer_busy_before_commit_reports_no_task_without_exposing_state(http, config, ready):
    cid, vid, *_ = ready
    locked, release = threading.Event(), threading.Event()

    def writer():
        with database.locked(config):
            locked.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(writer)
        try:
            assert locked.wait(5)
            response = http.post(
                "/admin/organization/maintenance",
                json={"client_id": cid, "vault_id": vid, "mode": "quick"},
            )
            assert response.status_code == 503, response.text
            assert response.json() == {"error": "maintenance_submission_not_started"}
            assert store.rows(config, "jobs", vid) == []
        finally:
            release.set()
            pending.result(5)


def test_concurrent_preparation_releases_writer_and_commits_one_task(http, config, ready):
    cid, vid, *_ = ready
    entered = threading.Barrier(3)
    release = threading.Event()
    original = maintenance_submission.prepare

    def slow(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.wait(10)
        assert release.wait(10)
        return value

    scope = {"client_id": cid, "vault_id": vid, "mode": "quick"}
    with patch.object(maintenance_submission, "prepare", slow):
        # HTTP imports the function explicitly; patch that boundary as well.
        with (
            patch("fiber_bundle.organization.maintenance_submission_http.prepare", slow),
            ThreadPoolExecutor(2) as pool,
        ):
            requests = [
                pool.submit(http.post, "/admin/organization/maintenance", json=scope)
                for _ in range(2)
            ]
            entered.wait(10)
            try:
                with database.lock_wait(0.2), database.locked(config):
                    assert not store.rows(config, "jobs", vid)
                assert http.get("/admin/vaults").status_code == 200
            finally:
                release.set()
            responses = [future.result(15) for future in requests]
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert sorted(r.json()["task_created"] for r in responses) == [False, True]
    assert len(store.rows(config, "jobs", vid)) == 1


@pytest.mark.parametrize("change", ["edit", "replace", "new", "revoke"])
def test_prepare_changes_create_no_task(http, config, ready, change):
    cid, vid, root, _, note = ready
    original = maintenance_submission.prepare

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        with database.locked(config), database.connect(config.state_root) as db:
            if change == "revoke":
                auth.set_grant(db, cid, vid, query=False, original=False, add=False)
            elif change == "new":
                (root / "Inbox/new.md").write_text("# new\n#热管理")
            elif change == "replace":
                replacement = note.with_suffix(".new")
                replacement.write_text("# Human replacement\n")
                replacement.replace(note)
            else:
                note.write_text("# Human edit\n")
        return value

    with patch("fiber_bundle.organization.maintenance_submission_http.prepare", changed):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    assert response.status_code in {403, 404, 409}, response.text
    if response.json()["error"] == "request_denied":
        assert set(response.json()) == {"error"}
    else:
        assert response.json()["task_created"] is False
    assert not store.rows(config, "jobs", vid)


def test_other_vault_changes_do_not_invalidate_submission(http, config, ready):
    cid, vid, *_ = ready
    original = maintenance_submission.prepare

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        with database.locked(config), database.connect(config.state_root) as db:
            other = db.execute("SELECT id FROM vaults WHERE id!=?", (vid,)).fetchone()[0]
            db.execute("UPDATE vaults SET name=name||' changed' WHERE id=?", (other,))
            store.put(
                config,
                "decisions",
                "unrelated-change",
                other,
                {"entity": "unrelated", "moc": "other", "state": "pinned"},
            )
        return value

    with patch("fiber_bundle.organization.maintenance_submission_http.prepare", changed):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["task_created"] is True


def test_catalog_pending_keeps_original_error_and_issues(http, config, ready):
    from fiber_bundle.organization.catalog_inventory import Unavailable

    cid, vid, *_ = ready
    issue = Unavailable("organization_catalog_pending", {"Inbox": "organization_directory_unknown"})
    with patch("fiber_bundle.organization.maintenance_submission_http.prepare", side_effect=issue):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "organization_catalog_pending"
    assert response.json()["issues"] == issue.issues
    assert response.json()["task_created"] is False


def test_projection_failure_returns_persisted_task_identity(http, config, ready):
    cid, vid, *_ = ready
    with patch(
        "fiber_bundle.organization.maintenance.public",
        side_effect=OSError("synthetic projection failure"),
    ):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    assert response.status_code == 503, response.text
    assert response.json()["error"] == "maintenance_submission_result_unavailable"
    assert response.json()["task_created"] is True
    task_id = response.json()["task_id"]
    assert [j["id"] for j in store.rows(config, "jobs", vid)] == [task_id]
    response = http.post(
        "/admin/organization/maintenance", json={"client_id": cid, "vault_id": vid, "mode": "quick"}
    )
    assert response.status_code == 200 and response.json()["id"] == task_id
    assert response.json()["task_created"] is False


def test_expired_delivery_never_exposes_persisted_task(http, config, ready):
    cid, vid, *_ = ready
    original = auth.admin
    checks = 0

    def expire(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 3:
            raise auth.Denied(401)
        return original(*args, **kwargs)

    with patch.object(auth, "admin", expire):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    assert response.status_code == 401
    assert response.json() == {"error": "request_denied"}
    assert len(store.rows(config, "jobs", vid)) == 1


@pytest.mark.parametrize("revoked", ["admin_session", "vault_capability"])
def test_projection_failure_then_actual_revocation_redacts_persisted_result(
    http, config, ready, revoked
):
    cid, vid, *_ = ready

    def revoked_projection(*args, **kwargs):
        writer = kwargs["db"]
        if revoked == "admin_session":
            writer.execute("DELETE FROM admin_sessions")
        else:
            auth.set_grant(writer, cid, vid, query=False, original=False, add=False)
        raise OSError("synthetic projection failure after revocation")

    with patch("fiber_bundle.organization.maintenance.public", revoked_projection):
        response = http.post(
            "/admin/organization/maintenance",
            json={"client_id": cid, "vault_id": vid, "mode": "quick"},
        )
    assert response.status_code in {401, 403, 404}, response.text
    assert response.json() == {"error": "request_denied"}
    assert len(store.rows(config, "jobs", vid)) == 1
