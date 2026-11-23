"""Actual SQLite mutation and rollback invalidate scoped compact projections."""

import sqlite3

import pytest
from test_jobs import ready as ready
from test_note_moc_locations import managed

from fiber_bundle import auth, database, note_inventory
from fiber_bundle import publication_inventory_read as projected
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical


def count_reads(db):
    calls = []
    db.set_trace_callback(lambda sql: calls.append(sql) if " AS projected," in sql else None)
    return calls


def test_repeated_cas_keeps_full_fresh_notes_but_projects_large_ledger_once(config, ready):
    cid, vid, root, _, note = ready
    _, _, _ = managed(config, ready)
    with database.connect(config.state_root) as db, projected.reuse(db):
        calls = count_reads(db)
        vault = auth.grant(db, cid, vid, "query")
        first = note_inventory.read(config, db, vault)
        # An unregistered unrelated note edit must be visible even within this unit.
        path = root / "Resource/new.md"
        path.write_text("# New external note\nOriginal\n")
        second = note_inventory.read(config, db, vault)
        path.write_text("# New external note\nModified\n")
        third = note_inventory.read(config, db, vault)
        assert len(calls) == 1
        assert "Resource/new.md" not in {n.relative_path for n in first.notes}
        assert next(n for n in second.notes if n.relative_path == "Resource/new.md").text.endswith(
            "Original\n"
        )
        assert next(n for n in third.notes if n.relative_path == "Resource/new.md").text.endswith(
            "Modified\n"
        )
        path.unlink()
        fourth = note_inventory.read(config, db, vault)
        assert {n.relative_path for n in fourth.notes} == {n.relative_path for n in first.notes}
        assert len(calls) == 1
    with database.connect(config.state_root) as db:
        calls = count_reads(db)
        list(projected.rows(db, vid))
        list(projected.rows(db, vid))
        assert len(calls) == 2  # No reuse leaks into independent requests.


@pytest.mark.parametrize("external", [False, True])
def test_content_change_without_revision_never_reuses_projection(config, ready, external):
    _, vid, *_ = ready
    identity, _, plan = managed(config, ready)
    with database.connect(config.state_root) as db, projected.reuse(db):
        calls = count_reads(db)
        original = list(projected.rows(db, vid))
        plan["body_files"] = []
        other = database.connect(config.state_root) if external else db
        try:
            other.execute(
                "UPDATE publication_locations SET value=? WHERE publication_id=?",
                (canonical(plan), identity),
            )
        finally:
            if external:
                other.close()
        changed = list(projected.rows(db, vid))
        assert len(calls) == 2 and changed != original
        assert changed[0][0]["revision"] == original[0][0]["revision"]
        assert changed[0][1]["body_files"] == []


def test_savepoint_and_transaction_rollback_do_not_retain_uncommitted_values(config, ready):
    _, vid, *_ = ready
    identity, _, plan = managed(config, ready)
    with database.connect(config.state_root) as db, projected.reuse(db):
        original = list(projected.rows(db, vid))
        db.execute("BEGIN")
        db.execute("SAVEPOINT change")
        plan["body_files"] = []
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        assert list(projected.rows(db, vid)) != original
        total = db.total_changes
        db.execute("ROLLBACK TO change")
        assert db.total_changes == total
        assert list(projected.rows(db, vid)) == original
        db.execute("ROLLBACK")
        assert list(projected.rows(db, vid)) == original


def test_callers_cannot_mutate_cached_projection_and_vaults_stay_separate(config, ready):
    _, vid, *_ = ready
    managed(config, ready)
    with database.connect(config.state_root) as db, projected.reuse(db):
        first = list(projected.rows(db, vid))
        expected = first[0][1]["body_files"].copy()
        first[0][1]["body_files"].clear()
        assert list(projected.rows(db, vid))[0][1]["body_files"] == expected
        assert list(projected.rows(db, "another-vault")) == []


def test_external_delete_add_and_malformed_record_are_observed(config, ready):
    _, vid, *_ = ready
    identity, _, _ = managed(config, ready)
    with database.connect(config.state_root) as db, projected.reuse(db):
        first = list(projected.rows(db, vid))
        with database.connect(config.state_root) as other:
            row = other.execute(
                "SELECT * FROM publication_locations WHERE publication_id=?", (identity,)
            ).fetchone()
            other.execute("DELETE FROM publication_locations WHERE publication_id=?", (identity,))
            assert list(projected.rows(db, vid)) == []
            other.execute("INSERT INTO publication_locations VALUES(?,?,?,?,?,?,?,?)", tuple(row))
            assert list(projected.rows(db, vid)) == first
            other.execute(
                "UPDATE publication_locations SET value='{}' WHERE publication_id=?",
                (identity,),
            )
            with pytest.raises(IntegrityError, match="projection invalid"):
                list(projected.rows(db, vid))


@pytest.mark.parametrize("external", [False, True])
def test_single_path_batch_projection_rechecks_actual_metadata_and_never_leaks_mutations(
    config, ready, external
):
    _, vid, *_ = ready
    identity, _, plan = managed(config, ready)
    path = plan["mapping"]["note.md"]
    with database.connect(config.state_root) as db, projected.reuse(db):
        calls = count_reads(db)
        first = projected.at_path(db, vid, path)
        expected = first["file_key"].copy()
        first["file_key"].clear()
        # Each reconcile call may opt in again without replacing its batch cache.
        with projected.reuse(db):
            assert projected.at_path(db, vid, path)["file_key"] == expected
        assert projected.at_path(db, vid, "missing.md") is None
        assert len(calls) == 1
        other = database.connect(config.state_root) if external else db
        try:
            plan["identity_overrides"] = {"note.md": "note:changed"}
            other.execute(
                "UPDATE publication_locations SET value=? WHERE publication_id=?",
                (canonical(plan), identity),
            )
            assert projected.at_path(db, vid, path)["identity"] == "note:changed"
            assert len(calls) == 2
        finally:
            if external:
                other.close()


def test_single_path_projection_does_not_retain_savepoint_values(config, ready):
    _, vid, *_ = ready
    identity, _, plan = managed(config, ready)
    path = plan["mapping"]["note.md"]
    with database.connect(config.state_root) as db, projected.reuse(db):
        first = projected.at_path(db, vid, path)
        db.execute("SAVEPOINT change")
        plan["body_files"] = []
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        assert projected.at_path(db, vid, path) is None
        db.execute("ROLLBACK TO change")
        assert projected.at_path(db, vid, path) == first
        db.execute("RELEASE change")
        assert projected.at_path(db, vid, path) == first


def test_replaced_sqlite_file_cannot_reuse_old_connection_projection(config, ready):
    _, vid, *_ = ready
    _, _, plan = managed(config, ready)
    with database.connect(config.state_root) as db, projected.reuse(db):
        projected.at_path(db, vid, plan["mapping"]["note.md"])
        replacement = config.state_root / "replacement.sqlite3"
        with sqlite3.connect(replacement) as target:
            db.backup(target)
        replacement.replace(config.state_root / "metadata.sqlite3")
        with pytest.raises(IntegrityError, match="publication_location_database_changed"):
            projected.at_path(db, vid, plan["mapping"]["note.md"])
