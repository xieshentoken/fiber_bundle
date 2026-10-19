import json

from test_jobs import ready as ready
from test_maintenance import preview
from test_note_moc_locations import managed

from fiber_bundle import database
from fiber_bundle.inputs import canonical
from fiber_bundle.organization import maintenance, maintenance_undo_state, store


def test_large_location_values_are_immutable_references_and_readonly_does_not_write(config, ready):
    cid, vid, *_ = ready
    identity, _, _ = managed(config, ready)
    home = config.state_root / "maintenance-undo-blobs"
    with database.connect(config.state_root) as db:
        row = db.execute(
            "SELECT value FROM publication_locations WHERE publication_id=?", (identity,)
        ).fetchone()
        plan = json.loads(row[0])
        plan["large_test_evidence"] = "large-private-payload" * 100000
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        compact = maintenance_undo_state.snapshot(config, db, vid)
        assert not home.exists()
        assert len(canonical(compact)) < 10000
        first = maintenance_undo_state.snapshot(config, db, vid, persist=True)
        paths = list(home.iterdir())
        stamps = {p.name: p.stat().st_mtime_ns for p in paths}
        second = maintenance_undo_state.snapshot(config, db, vid, persist=True)
        assert first == second and len(paths) == 1
        assert {p.name: p.stat().st_mtime_ns for p in home.iterdir()} == stamps
        store.put(
            config,
            "maintenance_undo",
            "compact-test",
            vid,
            {"before_state": first, "after_state": second},
        )
        with store.connection(config) as side:
            raw = side.execute(
                "SELECT value FROM maintenance_undo WHERE id='compact-test'"
            ).fetchone()[0]
        assert len(raw) < 20000 and "large-private-payload" not in raw


def test_corrupt_undo_blob_stops_before_restoring_any_vault_file(config, ready):
    cid, vid, root, moc, note = ready
    managed(config, ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        job = maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        journal = store.get(config, "maintenance_undo", job["id"])
        prior = next(iter(journal["before_state"]["locations"].values()))
        path = config.state_root / "maintenance-undo-blobs" / prior["value"]["undo_blob_sha256"]
        path.write_text("corrupted private snapshot")
        before = [p.read_bytes() for p in [moc, note]]
        maintenance.action(config, db, job, "cancel", None)
        job = maintenance.run(config, db, job)
        assert (
            job["state"] == "rollback_conflict"
            and job["error"] == "maintenance_undo_blob_unavailable"
        )
        assert [p.read_bytes() for p in [moc, note]] == before
