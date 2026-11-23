"""Archive finalization owns one committed journal, never another operation's lock."""

import json
from uuid import uuid4

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_moc_recovery import historical_map

from fiber_bundle import (
    cloud_observation,
    database,
    moc_file_journal,
    moc_placement_projection,
    moc_store,
    note_placement,
)
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import maintenance, maintenance_archive, store


def operation(db, vault, *, kind="maintenance_archive", state="applying", committed=True):
    identity = str(uuid4())
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,?,?,1,1)",
        (identity, vault, kind, state, json.dumps({"metadata_committed": committed})),
    )
    return identity


@pytest.mark.parametrize("kind", ["maintenance_archive", "note_placement"])
def test_finalization_accepts_only_own_committed_applying_operation(config, scope, kind):
    with database.connect(config.state_root) as db:
        identity = operation(db, scope[1], kind=kind)
        note_placement.guard(db, scope[1], finalizing_operation=identity)
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            note_placement.guard(db, scope[1])


@pytest.mark.parametrize("kind", ["maintenance_archive", "note_placement"])
@pytest.mark.parametrize("committed", [False, None])
def test_finalization_rejects_uncommitted_metadata(config, scope, kind, committed):
    with database.connect(config.state_root) as db:
        identity = operation(db, scope[1], kind=kind, committed=committed)
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            note_placement.guard(db, scope[1], finalizing_operation=identity)


@pytest.mark.parametrize("kind", ["maintenance_archive", "note_placement", "publication_rebind"])
def test_own_token_never_bypasses_foreign_pending_operation(config, scope, kind):
    with database.connect(config.state_root) as db:
        identity = operation(db, scope[1])
        operation(db, scope[1], kind=kind)
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            note_placement.guard(db, scope[1], finalizing_operation=identity)


@pytest.mark.parametrize("state", ["prepared", "reconcile", "completed"])
def test_finalization_token_requires_applying_state(config, scope, state):
    with database.connect(config.state_root) as db:
        identity = operation(db, scope[1], state=state)
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            note_placement.guard(db, scope[1], finalizing_operation=identity)


def test_finalization_token_cannot_cross_vault_or_operation_kind(config, scope):
    with database.connect(config.state_root) as db:
        other = db.execute("SELECT id FROM vaults WHERE id<>? LIMIT 1", (scope[1],)).fetchone()[0]
        identity = operation(db, other)
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            note_placement.guard(db, scope[1], finalizing_operation=identity)
        wrong_kind = operation(db, scope[1], kind="maintenance_link_repair")
        with pytest.raises(IntegrityError, match="placement_reconciliation_required"):
            note_placement.guard(db, scope[1], finalizing_operation=wrong_kind)


@pytest.mark.parametrize("point", ["before_journal", "after_journal"])
def test_archive_resume_finalizes_committed_move_once(config, ready, monkeypatch, point):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        mid, human_id, human = historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        original = moc_placement_projection.finalize

        def interrupted(config, db, identity, value, root):
            row = db.execute(
                "SELECT kind,state,payload FROM vault_operations WHERE id=?", (identity,)
            ).fetchone()
            assert row["kind"] == "maintenance_archive" and row["state"] == "applying"
            assert value["move_applied"] and json.loads(row["payload"])["metadata_committed"]
            if point == "after_journal":
                original(config, db, identity, value, root)
            raise OSError("interrupted archive finalization")

        monkeypatch.setattr(moc_placement_projection, "finalize", interrupted)
        failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial", failed
        item = failed["items"][0]
        identity = item["archive_operation"]
        row = db.execute(
            "SELECT state,payload FROM vault_operations WHERE id=?", (identity,)
        ).fetchone()
        assert row["state"] == "reconcile"
        assert json.loads(row["payload"])["metadata_committed"] is True
        assert not note.exists() and (root / "Resource/传热.md").exists()
        members = moc_store.load(config, vid, mid)
        monkeypatch.setattr(moc_placement_projection, "finalize", original)
        resumed = maintenance.action(config, db, failed, "resume", None)
        done = maintenance.run(config, db, store.get(config, "jobs", resumed["id"]))
        assert done["state"] == "completed", done
        assert not done["model_called"]
        assert moc.read_bytes().startswith(human)
        assert moc_store.load(config, vid, mid) == members
        assert members.members[human_id] == ["manual"]
        row = db.execute("SELECT * FROM vault_operations WHERE id=?", (identity,)).fetchone()
        assert row["state"] == "completed"
        before = tuple(row), moc.read_bytes(), len(moc_file_journal.plans(config, vid, mid))
        maintenance_archive.apply(config, db, done, done["items"][0])
        assert before == (
            tuple(db.execute("SELECT * FROM vault_operations WHERE id=?", (identity,)).fetchone()),
            moc.read_bytes(),
            len(moc_file_journal.plans(config, vid, mid)),
        )


@pytest.mark.parametrize("outcome", ["recover", "exhaust", "conflict"])
def test_worker_rechecks_only_committed_archive(config, ready, monkeypatch, outcome):
    from fiber_bundle import note_placement_execution
    from fiber_bundle.organization import maintenance_archive_recheck

    cid, vid, root, moc, note = ready
    monkeypatch.setattr(maintenance_archive_recheck, "DELAYS", (0, 0, 0))
    finalize = moc_placement_projection.finalize
    attempts = []

    def unstable(config, db, identity, value, root):
        attempts.append(identity)
        with cloud_observation.scope(root), cloud_observation.phase("archive_finalize_inventory"):
            if len(attempts) == 1 or outcome == "exhaust":
                error = cloud_observation.ObservationError(
                    "note directory changed", root, "directory_changed", changed=["mtime_ns"]
                )
                error.detail["retryable_read"] = True
                raise error
            if outcome == "conflict":
                raise cloud_observation.ObservationError(
                    "note directory entries changed", root, "directory_entries_changed"
                )
        return finalize(config, db, identity, value, root)

    with database.connect(config.state_root) as db:
        historical_map(config, db, ready)
        job = preview(config, db, cid, vid, "quick")
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        monkeypatch.setattr(moc_placement_projection, "finalize", unstable)
        pending = maintenance.run(config, db, job)
        assert pending["state"] == "running"
        assert pending["archive_recheck"]["attempt"] == 1
        identity = pending["items"][0]["archive_operation"]
        target = root / "Resource/传热.md"
        before = (target.stat().st_ino, target.read_bytes(), moc.read_bytes())
        assert not note.exists()

    def replay(*args, **kwargs):
        raise AssertionError("archive write replay")

    monkeypatch.setattr(note_placement_execution, "move", replay)
    monkeypatch.setattr(note_placement_execution, "exchange", replay)
    monkeypatch.setattr(note_placement_execution, "complete", replay)
    # Each iteration reloads the persisted job on a fresh connection.
    for _ in range(5):
        with database.connect(config.state_root) as db:
            job = store.get(config, "jobs", job["id"])
            if job["state"] != "running":
                break
            maintenance.run(config, db, job)
    assert "archive_recheck" not in job
    assert (target.stat().st_ino, target.read_bytes(), moc.read_bytes()) == before
    assert not job["model_called"]
    with database.connect(config.state_root) as db:
        state = db.execute("SELECT state FROM vault_operations WHERE id=?", (identity,)).fetchone()[
            0
        ]
    if outcome == "recover":
        assert (job["state"], state, attempts) == ("completed", "completed", [identity] * 2)
    else:
        assert (job["state"], state) == ("partial", "reconcile")
        assert len(attempts) == (4 if outcome == "exhaust" else 2)
