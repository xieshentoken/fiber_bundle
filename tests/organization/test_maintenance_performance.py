"""Observable work counts: frozen previews, compact undo and bounded scans."""

from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import database
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import engine, maintenance, model_review


def test_compact_undo_keeps_large_unrelated_history_and_can_cancel(config, ready):
    import json

    from fiber_bundle.organization import maintenance_undo, store

    cid, vid, _, _, note = ready
    history = {"text": "private-history" * 100000}
    with database.connect(config.state_root) as db:
        store.put(config, "versions", "retained", vid, history)
        before = note.read_bytes()
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        maintenance.run(config, db, job)
        journal = store.get(config, "maintenance_undo", job["id"])
        assert journal["snapshot_version"] == 2
        assert len(json.dumps(journal)) < 30000
        assert "undo_blob_sha256" in journal["before_state"]["organization"]["versions"]["retained"]
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance_undo.run(config, db, job)
        assert result["state"] == "cancelled", result
        assert note.read_bytes() == before
        assert store.get(config, "versions", "retained") == history


def test_legacy_inline_undo_remains_resumable(config, ready):
    from fiber_bundle.organization import maintenance_undo_state as state
    from fiber_bundle.organization import store

    cid, vid, _, _, note = ready
    with database.connect(config.state_root) as db:
        baseline = state.snapshot(config, db, vid, persist=True)
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        # Simulate an existing pre-upgrade journal; keep its original representation.
        store.put(
            config,
            "maintenance_undo",
            job["id"],
            vid,
            {
                "id": job["id"],
                "state": "ready",
                "files": {},
                "moves": [],
                "plans": [],
                "operations": [],
                "before_state": baseline,
                "after_state": baseline,
            },
        )
        before = note.read_bytes()
        result = maintenance.run(config, db, job)
        assert result["state"] == "completed", result
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "cancelled", result
        assert note.read_bytes() == before


def test_managed_archive_reuses_the_inventory_with_fresh_execution_check(config, ready):
    from test_note_moc_locations import managed

    from fiber_bundle import note_inventory

    cid, vid, *_ = ready
    managed(config, ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(note_inventory, "read", wraps=note_inventory.read) as reads:
            from fiber_bundle import note_placement

            original = note_placement.preview

            def shared(*args, **kwargs):
                inventory = kwargs.get("inventory")
                assert inventory is not None
                before = reads.call_count
                value = original(*args, **kwargs)
                assert reads.call_count == before  # Reuse planning, retain later CAS reads.
                return value

            with patch.object(note_placement, "preview", side_effect=shared) as planned:
                result = maintenance.run(config, db, job)
        assert result["state"] == "completed", result
        assert planned.call_count == 1
        assert reads.call_count > 1  # Independent MOC and execution checks remain active.


def test_corrupt_compact_payload_stops_cancellation_before_file_changes(config, ready):
    from fiber_bundle.organization import store

    cid, vid, root, moc, note = ready
    note.write_text(note.read_text() + "\n热管理的正文。" * 1000)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        maintenance.run(config, db, job)
        journal = store.get(config, "maintenance_undo", job["id"])
        row = next(iter(journal["after_state"]["organization"]["note_moc"].values()))
        (config.state_root / "maintenance-undo-blobs" / row["undo_blob_sha256"]).write_text("bad")
        moved = root / job["items"][0]["target"]
        before = [p.read_bytes() for p in [moc, moved]]
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "rollback_conflict", result
        assert result["error"] == "maintenance_undo_blob_unavailable"
        assert [p.read_bytes() for p in [moc, moved]] == before


def test_confirmation_reuses_analysis_without_local_or_model_inference(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        frozen = [(i["selected"], i["candidates"]) for i in job["items"]]
        with (
            patch.object(engine, "infer", side_effect=AssertionError("repeated inference")),
            patch.object(model_review, "resolve", side_effect=AssertionError("model send")),
        ):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
            result = maintenance.run(config, db, job)
        assert result["state"] == "completed", result
        assert [(i["selected"], i["candidates"]) for i in result["items"]] == frozen


@pytest.mark.parametrize("change", ["note", "topic", "manual"])
def test_light_confirmation_still_rejects_changed_inputs(config, ready, change):
    from fiber_bundle.organization import store

    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        if change == "note":
            note.write_text(note.read_text() + "\n新的正文。")
        elif change == "topic":
            moc.write_text(moc.read_text() + "\n新的主题定义。")
        else:
            item = job["items"][0]
            store.put(
                config,
                "decisions",
                "human",
                vid,
                {"entity": item["entity"], "moc": item["selected"][0], "state": "pinned"},
            )
        with pytest.raises(IntegrityError, match="preview_changed"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])


def test_seal_batches_full_evidence_without_per_file_lock_handoffs(config, ready, monkeypatch):
    from test_execution_leases import confirmed

    from fiber_bundle.organization import execution, maintenance_undo_files, store

    job = confirmed(config, ready, two=True)
    original_stamp = execution.stamp
    original_observe = maintenance_undo_files.observe
    stamps, observed = [], []

    def version(*args):
        from fiber_bundle import storage_read_scope

        callback = storage_read_scope.current.get()
        if callback and callback.__self__.job.get("activity", {}).get("phase") == "undo_seal":
            stamps.append(1)
        return original_stamp(*args)

    def observe(root, path):
        observed.append((root, path))
        return original_observe(root, path)

    monkeypatch.setattr(execution, "stamp", version)
    monkeypatch.setattr(maintenance_undo_files, "observe", observe)
    execution.run(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert result["completed"] == 1, result.get("error")
    journal = store.get(config, "maintenance_undo", job["id"])
    assert journal["state"] == "ready"
    assert len(stamps) <= 4  # A bounded handoff, independent of retained evidence count.
    assert len(observed) > len(stamps)
    assert {(ready[2], path) for path in journal["files"]} <= set(observed)


def test_seal_rejects_concurrent_metadata_before_persisting_evidence(config, ready, monkeypatch):
    from test_execution_leases import confirmed

    from fiber_bundle import storage_read_scope
    from fiber_bundle.organization import execution, maintenance_undo_files, store

    job = confirmed(config, ready)
    original = maintenance_undo_files.observe
    changed = False

    def observe(root, path):
        nonlocal changed
        result = original(root, path)
        callback = storage_read_scope.current.get()
        if (
            not changed
            and callback
            and callback.__self__.reading
            and callback.__self__.job.get("activity", {}).get("phase") == "undo_seal"
        ):
            changed = True
            # This writer runs under the same real lease used by other processes.
            with database.locked(config):
                store.put(config, "policies", "external", job["vault_id"], {"changed": True})
        return result

    monkeypatch.setattr(maintenance_undo_files, "observe", observe)
    execution.run(config, job["id"])
    result = store.get(config, "jobs", job["id"])
    assert changed and result["state"] == "partial"
    assert "organization_execution_inputs_changed" in result["error"]
    assert store.get(config, "maintenance_undo", job["id"])["state"] == "applying"
    assert store.get(config, "policies", "external") == {"changed": True}
