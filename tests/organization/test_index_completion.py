import time
from unittest.mock import patch

from test_jobs import ready as ready
from test_vault_sync_reconcile import observe

from fiber_bundle import database, note_search_jobs
from fiber_bundle.organization import index_completion, store
from fiber_bundle.vault_sync import index, scanner
from fiber_bundle.vault_sync import store as sync_store


def test_completion_joins_pending_observer_then_settles_without_rebuild(config, ready):
    cid, vid, root, *_ = ready
    observe(config, ({"id": cid}, vid, root))
    scanner.heartbeat(config, vid)
    job = dict(
        id="completed-job",
        client_id=cid,
        vault_id=vid,
        state="completed",
        index_state="pending",
        completed=29,
        updated_at=time.time(),
        items=[],
    )
    with database.locked(config):
        store.put(config, "jobs", job["id"], vid, job)
    with patch.object(index, "update", side_effect=AssertionError("organization must not build")):
        index_completion.advance(config, job)
        requested = store.get(config, "jobs", job["id"])
        assert requested["index_state"] == "pending"
        token = note_search_jobs.read(config, vid)["id"]
        index_completion.advance(config, requested)
        assert note_search_jobs.read(config, vid)["id"] == token
    # The independent observer executes the real incremental index writer.
    index.update(config, cid, vid)
    index_completion.advance(config, store.get(config, "jobs", job["id"]))
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "completed" and result["completed"] == 29
    assert result["index_state"] == "ready" and result["index_error"] is None


def test_pending_file_events_are_waiting_and_true_capacity_failure_is_reported(config, ready):
    cid, vid, root, *_ = ready
    observe(config, ({"id": cid}, vid, root))
    scanner.heartbeat(config, vid)
    sync_store.enqueue(config, vid, [("Inbox/传热.md", 2)], "100")
    job = dict(
        id="waiting-index",
        client_id=cid,
        vault_id=vid,
        state="completed",
        index_state="pending",
        updated_at=time.time(),
        completed=1,
    )
    with database.locked(config):
        store.put(config, "jobs", job["id"], vid, job)
    index_completion.advance(config, job)
    requested = store.get(config, "jobs", job["id"])
    index_completion.advance(config, requested)
    assert store.get(config, "jobs", job["id"])["index_state"] == "pending"
    with sync_store.connection(config, write=True) as db:
        db.execute("UPDATE libraries SET error='vault_sync_index_capacity' WHERE id=?", (vid,))
    index_completion.advance(config, requested)
    result = store.get(config, "jobs", job["id"])
    assert result["state"] == "completed" and result["index_state"] == "failed"
    assert result["index_error"] == "vault_sync_index_capacity"
