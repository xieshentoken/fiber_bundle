"""Real organization execution, concurrent HTTP, and recovery on synthetic files."""

import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import auth, database, note_index, note_placement_execution
from fiber_bundle.api import create_app
from fiber_bundle.organization import execution, execution_control, maintenance, store


def confirmed(config, ready, *, two=False):
    cid, vid, root, _, _ = ready
    if two:
        (root / "Inbox/第二篇.md").write_text("# 第二篇\n\n#热管理\n这是第二篇传热笔记。\n")
    with database.locked(config), database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        return maintenance.action(config, db, job, "confirm", job["plan_sha256"])


def client(config):
    auth.set_password(config, "synthetic-execution-test")
    c = TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345)
    )
    c.headers["Origin"] = "http://127.0.0.1:8765"
    r = c.post("/admin/login", json={"password": "synthetic-execution-test"})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def blocked_inventory(config, job):
    entered, release = threading.Event(), threading.Event()
    original = note_index.snapshot
    first = True

    def slow(*args, **kwargs):
        nonlocal first
        if first and threading.current_thread().name == "synthetic-organizer":
            first = False
            entered.set()
            assert release.wait(20)
        return original(*args, **kwargs)

    worker = threading.Thread(
        target=execution.run, args=(config, job["id"]), name="synthetic-organizer"
    )
    return slow, worker, entered, release


def test_slow_execution_progress_pause_and_other_pages_are_available(config, ready):
    job = confirmed(config, ready, two=True)
    c = client(config)
    slow, worker, entered, release = blocked_inventory(config, job)
    scope = {"client_id": job["client_id"], "vault_id": job["vault_id"]}
    with patch.object(note_index, "snapshot", slow):
        worker.start()
        try:
            assert entered.wait(10)
            start = time.monotonic()
            r = c.get("/admin/organization/progress", params=scope)
            assert r.status_code == 200, r.text
            assert any(j["id"] == job["id"] and j["state"] == "running" for j in r.json()["jobs"])
            assert c.get("/admin/vaults").status_code == 200
            r = c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope)
            assert r.status_code == 200, r.text
            assert r.json()["pause_requested"]
            assert time.monotonic() - start < 3
            # A second process/tick may not execute the unlocked unit again.
            execution.run(config, job["id"])
            assert store.get(config, "jobs", job["id"])["completed"] == 0
            assert c.get("/admin/organization/progress", params=scope).json()["jobs"][0][
                "pause_requested"
            ]
        finally:
            release.set()
            worker.join(20)
    assert not worker.is_alive()
    paused = store.get(config, "jobs", job["id"])
    assert paused["state"] == "paused", paused.get("error")
    assert paused["completed"] == 1
    assert store.get(config, "maintenance_undo", job["id"])["state"] == "ready"
    assert not execution_control.requested(config, paused)
    with database.locked(config), database.connect(config.state_root) as db:
        maintenance.action(config, db, paused, "resume", None)
    execution.run(config, job["id"])
    done = store.get(config, "jobs", job["id"])
    assert done["state"] == "completed", done.get("error")
    assert done["completed"] == 2
    before = {str(p): p.read_bytes() for p in ready[2].rglob("*") if p.is_file()}
    execution.run(config, job["id"])
    assert before == {str(p): p.read_bytes() for p in ready[2].rglob("*") if p.is_file()}
    c.close()


def test_revoked_permission_during_unlocked_read_prevents_writes(config, ready):
    job = confirmed(config, ready)
    before = {str(p): p.read_bytes() for p in ready[2].rglob("*") if p.is_file()}
    slow, worker, entered, release = blocked_inventory(config, job)
    with patch.object(note_index, "snapshot", slow):
        worker.start()
        try:
            assert entered.wait(10)
            with database.locked(config), database.connect(config.state_root) as db:
                auth.set_grant(
                    db, job["client_id"], job["vault_id"], query=False, original=False, add=False
                )
        finally:
            release.set()
            worker.join(20)
    assert not worker.is_alive()
    current = store.get(config, "jobs", job["id"])
    assert current["state"] == "partial"
    assert current["completed"] == 0
    assert before == {str(p): p.read_bytes() for p in ready[2].rglob("*") if p.is_file()}


def test_interrupted_move_resumes_original_journal_without_reapplying(config, ready):
    job = confirmed(config, ready)
    original = note_placement_execution.move
    calls = []

    def interrupted(*args):
        original(*args)
        calls.append(args[1])
        raise OSError("synthetic crash after move")

    with patch.object(note_placement_execution, "move", interrupted):
        execution.run(config, job["id"])
    failed = store.get(config, "jobs", job["id"])
    assert failed["state"] == "partial"
    operation = failed["items"][0]["archive_operation"]
    assert not ready[4].exists()
    assert (ready[2] / "Resource/传热.md").exists()
    with database.locked(config), database.connect(config.state_root) as db:
        maintenance.action(config, db, failed, "resume", None)
    execution.run(config, job["id"])
    done = store.get(config, "jobs", job["id"])
    assert done["state"] == "completed", done.get("error")
    assert done["items"][0]["archive_operation"] == operation
    assert len(calls) == 1


def test_progress_does_not_revalidate_old_previews(config, ready, monkeypatch):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        preview(config, db, cid, vid, "quick")
    c = client(config)
    monkeypatch.setattr(
        maintenance, "validate", lambda *a, **k: pytest.fail("progress scanned a vault")
    )
    r = c.get("/admin/organization/progress", params={"client_id": cid, "vault_id": vid})
    assert r.status_code == 200, r.text
    assert r.json()["jobs"][0]["state"] == "preview"
    c.close()


def test_pause_intent_survives_worker_exit_without_reconfirming(config, ready):
    job = confirmed(config, ready)
    with execution_control.claim(config, job["id"]) as acquired:
        assert acquired
        with database.locked(config), database.connect(config.state_root) as db:
            maintenance.action(config, db, job, "pause", None)
    execution.run(config, job["id"])
    paused = store.get(config, "jobs", job["id"])
    assert paused["state"] == "paused" and paused["completed"] == 0
    assert paused["confirmed_sha256"] == job["confirmed_sha256"]
    assert ready[4].exists()


def test_external_edit_is_preserved_during_unlocked_read(config, ready):
    job = confirmed(config, ready)
    slow, worker, entered, release = blocked_inventory(config, job)
    edited = ready[4].read_bytes() + b"\nHuman edit during preparation\n"
    with patch.object(note_index, "snapshot", slow):
        worker.start()
        try:
            assert entered.wait(10)
            ready[4].write_bytes(edited)
        finally:
            release.set()
            worker.join(20)
    current = store.get(config, "jobs", job["id"])
    assert current["state"] == "partial"
    assert ready[4].read_bytes() == edited
    assert not (ready[2] / "Resource/传热.md").exists()


def test_large_scope_batches_cloud_metadata_and_keeps_progress_responsive(
    config, ready, monkeypatch
):
    from fiber_bundle import cloud_files, storage_read_scope

    root = ready[2]
    for n in range(650):
        (root / f"Resource/note-{n}.md").write_text(f"# Note {n}\nSynthetic content\n")
    assets = root / "Resource/assets"
    assets.mkdir()
    for n in range(1500):
        (assets / f"image-{n}.png").write_bytes(b"synthetic image")
    job = confirmed(config, ready, two=True)
    c = client(config)
    real_one, real_many = cloud_files.require_available, cloud_files.require_available_many
    batches, singles = [], []
    busy = threading.Event()

    def one(path, **kwargs):
        if path.is_relative_to(root):
            singles.append(path)
            owner = storage_read_scope.current.get()
            if owner and owner.__self__.reading:
                busy.set()
            time.sleep(0.001)
        return real_one(path, **kwargs)

    def many(parent, names):
        batches.extend(parent / n for n in names)
        busy.set()
        time.sleep(0.002)
        return real_many(parent, names)

    monkeypatch.setattr(cloud_files, "require_available", one)
    monkeypatch.setattr(cloud_files, "require_available_many", many)
    worker = threading.Thread(target=execution.run, args=(config, job["id"]))
    worker.start()
    try:
        assert busy.wait(15)
        scope = {"client_id": job["client_id"], "vault_id": job["vault_id"]}
        for _ in range(3):
            start = time.monotonic()
            response = c.get("/admin/organization/progress", params=scope)
            assert response.status_code == 200, response.text
            assert time.monotonic() - start < 1
        response = c.post(f"/admin/organization/maintenance/{job['id']}/pause", json=scope)
        assert response.status_code == 200, response.text
        assert c.get("/admin/vaults").status_code == 200
    finally:
        worker.join(60)
        c.close()
    assert not worker.is_alive()
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "paused", result.get("error")
    assert result["completed"] == 1
    assert set(assets.iterdir()) <= set(batches), (
        len(batches),
        len(singles),
        result.get("timings"),
    )
    # Read preparation batches all authorized attachments; actual note writes still
    # perform fresh individual availability checks and the existing content CAS.
    assert not any(p.parent == assets for p in singles)
    assert any(p.name == "热管理 MOC.md" for p in singles)


def test_crash_during_seal_does_not_publish_premature_completion(config, ready, monkeypatch):
    from fiber_bundle import cloud_files, storage_read_scope

    class PowerLoss(BaseException):
        pass

    job = confirmed(config, ready)
    original = cloud_files.require_available
    crashed = False

    def failure(path, **kwargs):
        nonlocal crashed
        callback = storage_read_scope.current.get()
        owner = callback.__self__ if callback else None
        if (
            owner
            and not crashed
            and owner.job.get("state") == "completed"
            and owner.job.get("activity", {}).get("phase") == "undo_seal"
            and path.name == "传热.md"
        ):
            crashed = True
            raise PowerLoss()
        return original(path, **kwargs)

    monkeypatch.setattr(cloud_files, "require_available", failure)
    with pytest.raises(PowerLoss):
        execution.run(config, job["id"])
    interrupted = store.get(config, "jobs", job["id"])
    assert interrupted["state"] == "running"
    assert interrupted["completed"] == 0
    assert store.get(config, "maintenance_undo", job["id"])["state"] == "applying"
    moved = ready[2] / "Resource/传热.md"
    bytes_after_move = moved.read_bytes()
    execution.run(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "completed", result.get("error")
    assert result["completed"] == 1
    assert moved.read_bytes() == bytes_after_move
    assert store.get(config, "maintenance_undo", job["id"])["state"] == "ready"


def test_progress_keeps_admin_and_vault_permission_checks(config, ready):
    job = confirmed(config, ready)
    c = client(config)
    scope = {"client_id": job["client_id"], "vault_id": job["vault_id"]}
    with database.locked(config), database.connect(config.state_root) as db:
        auth.set_grant(
            db, job["client_id"], job["vault_id"], query=False, original=False, add=False
        )
    denied = c.get("/admin/organization/progress", params=scope)
    assert denied.status_code in {403, 404}
    assert job["id"] not in denied.text
    c.cookies.clear()
    denied = c.get("/admin/organization/progress", params=scope)
    assert denied.status_code == 401
    assert job["id"] not in denied.text
    c.close()


@pytest.mark.parametrize(
    "table,expected", [("package_scans", "completed"), ("policies", "partial")]
)
def test_only_book_scan_status_is_ignored_during_unlocked_execution(config, ready, table, expected):
    job = confirmed(config, ready)
    slow, worker, entered, release = blocked_inventory(config, job)
    with patch.object(note_index, "snapshot", slow):
        worker.start()
        try:
            assert entered.wait(10)
            with database.locked(config):
                store.put(
                    config,
                    table,
                    "synthetic-scan",
                    job["vault_id"],
                    {"id": "synthetic-scan", "state": "ready", "checked_at": time.time()},
                )
        finally:
            release.set()
            worker.join(20)
    assert not worker.is_alive()
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == expected, result.get("error")
    assert result["completed"] == (1 if expected == "completed" else 0)
    if expected == "partial":
        assert "organization_execution_inputs_changed" in result["error"]
        assert ready[4].exists()


def test_pause_is_retained_if_process_exits_before_ledger_commit(config, ready, monkeypatch):
    class PowerLoss(BaseException):
        pass

    job = confirmed(config, ready)
    with database.locked(config):
        execution_control.request(config, job)
    original = store.put

    def fail(config, table, identity, vault, value):
        if table == "jobs" and value.get("state") == "paused":
            raise PowerLoss()
        return original(config, table, identity, vault, value)

    with monkeypatch.context() as m:
        m.setattr(store, "put", fail)
        with pytest.raises(PowerLoss):
            execution.run(config, job["id"])
    pending = store.get(config, "jobs", job["id"])
    assert pending == job
    assert execution_control.requested(config, pending)
    execution.run(config, job["id"])
    paused = store.get(config, "jobs", job["id"])
    assert paused["state"] == "paused" and paused["completed"] == 0
    assert ready[4].exists()
