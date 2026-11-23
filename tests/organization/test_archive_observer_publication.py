"""Real placement writes publish bounded, durable observer work without native file events."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import auth, database, placement_sync, vault_scope
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import execution, maintenance, maintenance_archive, store
from fiber_bundle.vault_sync import index, scanner
from fiber_bundle.vault_sync import store as sync


def drain(config, vault):
    now = time.time() + 20
    for _ in range(100):
        tasks = sync.due(config, now=now)
        if not tasks:
            break
        for task in tasks:
            assert task["vault"] == vault
            scanner.apply(config, task, now=now)
        now += 1
    else:
        pytest.fail("observer queue did not converge")
    scanner.heartbeat(config, vault)
    assert sync.status(config, vault)["active_pending"] == 0


def observed(config, ready):
    cid, vid, root, *_ = ready
    (root / "Resource/inbound.md").write_text("# Human inbound\n[[Inbox/传热]]\n")
    with database.connect(config.state_root) as db:
        scope = vault_scope.for_vault(root, auth.grant(db, cid, vid, "query"))
    sync.register(config, vid, str(root), scope, "synthetic-observer")
    drain(config, vid)
    index.update(config, cid, vid)
    with database.locked(config), database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    return job


def archived(config, ready, job):
    execution.run(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "completed", result.get("error")
    assert result["completed"] == 1
    return result


def test_own_move_drains_existing_observer_and_next_additive_is_current(config, ready):
    cid, vid, root, _, original = ready
    job = archived(config, ready, observed(config, ready))
    assert not original.exists()
    assert (root / "Resource/传热.md").is_file()
    assert "[[Resource/传热]]" in (root / "Resource/inbound.md").read_text()
    with sync.connection(config) as side:
        pending = {r["path"]: r["flags"] for r in side.execute("SELECT * FROM pending")}
    assert {
        "Inbox/传热.md",
        "Resource/传热.md",
        "Map/热管理 MOC.md",
        "Resource/inbound.md",
    } <= pending.keys()
    assert {"", "Inbox", "Resource", "Map"} <= pending.keys()
    assert not pending[""] & 1
    drain(config, vid)
    index.update(config, cid, vid)
    status = sync.status(config, vid)
    assert status["generation"] == status["indexed"] and status["pending"] == 0
    with sync.connection(config) as side:
        paths = {r[0] for r in side.execute("SELECT path FROM files WHERE directory=0")}
    assert "Resource/传热.md" in paths and "Inbox/传热.md" not in paths
    with database.locked(config), database.connect(config.state_root) as db:
        next_job = maintenance.submit(config, db, cid, vid, "additive")
    assert any(i["path"] == "Resource/传热.md" for i in next_job["items"])
    assert not job["model_called"]


@pytest.mark.parametrize("after_enqueue", [False, True])
def test_notification_failure_recovers_original_operation_and_is_idempotent(
    config, ready, after_enqueue
):
    cid, vid, root, *_ = ready
    job = observed(config, ready)
    original = sync.enqueue
    fired = False

    def interrupted(*args, **kwargs):
        nonlocal fired
        if kwargs.get("notification_id") and not fired:
            fired = True
            if after_enqueue:
                original(*args, **kwargs)
            raise OSError("synthetic observer publication interruption")
        return original(*args, **kwargs)

    with patch.object(sync, "enqueue", interrupted):
        execution.run(config, job["id"])
    interrupted_job = store.get(config, "jobs", job["id"])
    assert interrupted_job["state"] == "partial"
    operation = interrupted_job["items"][0]["archive_operation"]
    with database.connect(config.state_root) as db:
        row = db.execute(
            "SELECT state,payload FROM vault_operations WHERE id=?", (operation,)
        ).fetchone()
    assert row["state"] == "reconcile" and json.loads(row["payload"])["metadata_committed"]
    generation = sync.status(config, vid)["generation"]
    bytes_after_write = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.md")}
    with database.locked(config), database.connect(config.state_root) as db:
        maintenance.action(config, db, interrupted_job, "resume", None)
    # A fresh unit reconstructs from the original durable operation, with no cached read session.
    done = archived(config, ready, interrupted_job)
    assert done["items"][0]["archive_operation"] == operation
    assert {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.md")
    } == bytes_after_write
    if after_enqueue:
        assert sync.status(config, vid)["generation"] == generation
    with database.locked(config), database.connect(config.state_root) as db:
        maintenance_archive.apply(config, db, done, done["items"][0])
    generation = sync.status(config, vid)["generation"]
    with database.locked(config), database.connect(config.state_root) as db:
        maintenance_archive.apply(config, db, done, done["items"][0])
    assert sync.status(config, vid)["generation"] == generation
    with sync.connection(config) as side:
        assert side.execute("SELECT count(*) FROM committed_notifications").fetchone()[0] == 1
    drain(config, vid)
    index.update(config, cid, vid)
    with database.connect(config.state_root) as db:
        maintenance.submit(config, db, cid, vid, "additive")


def test_notification_rechecks_revoked_capability_before_queue_publication(config, ready):
    cid, vid, root, *_ = ready
    done = archived(config, ready, observed(config, ready))
    operation = done["items"][0]["archive_operation"]
    with database.connect(config.state_root) as db:
        value = json.loads(
            db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
        )
        auth.set_grant(db, cid, vid, query=False, original=False, add=False)
        before = sync.status(config, vid)
        with patch.object(sync, "enqueue", side_effect=AssertionError("unauthorized queue write")):
            with pytest.raises(auth.Denied):
                placement_sync.committed(config, db, operation, value, root)
        assert sync.status(config, vid) == before


def test_notification_scope_tightening_never_stats_denied_note(config, ready):
    cid, vid, root, *_ = ready
    done = archived(config, ready, observed(config, ready))
    operation = done["items"][0]["archive_operation"]
    with database.connect(config.state_root) as db:
        value = json.loads(
            db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
        )
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["Resource"]}), vid),
        )
        before = sync.status(config, vid)
        original = Path.lstat

        def allowed(path):
            assert not path.is_relative_to(root / "Resource"), "denied child metadata"
            return original(path)

        with patch.object(Path, "lstat", allowed):
            with pytest.raises(IntegrityError, match="scope_denied"):
                placement_sync.committed(config, db, operation, value, root)
        assert sync.status(config, vid) == before


def test_own_notification_receipt_preserves_cursor_and_rejects_changed_events(config, ready):
    cid, vid, root, *_ = ready
    observed(config, ready)
    with sync.connection(config, write=True) as side:
        side.execute("UPDATE libraries SET cursor='900' WHERE id=?", (vid,))
    sync.enqueue(config, vid, [("Inbox/传热.md", 1)], "1", notification_id="synthetic-owned-write")
    current = sync.status(config, vid)
    assert sync.library(config, vid)["cursor"] == "900"
    sync.enqueue(config, vid, [("Inbox/传热.md", 1)], "2", notification_id="synthetic-owned-write")
    assert sync.status(config, vid) == current
    with pytest.raises(IntegrityError, match="notification_changed"):
        sync.enqueue(
            config, vid, [("Resource/changed.md", 1)], "3", notification_id="synthetic-owned-write"
        )
    assert sync.status(config, vid) == current
