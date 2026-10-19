"""Existing flat notes can be organized without enabling new publication."""

import json

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_maintenance_apply import execute
from test_note_moc_locations import managed

from fiber_bundle import (
    database,
    moc_registration,
    note_placement,
    publication_locations,
    vault_save_policy,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.organization import maintenance, note_moc_fields


def stale_binding(config, db, vid):
    row = db.execute("SELECT value FROM vault_save_policies WHERE vault_id=?", (vid,)).fetchone()
    value = json.loads(row[0])
    # Simulate a directory identity left behind by a legacy rule binding.
    value["directories"]["Book"][1] += 1
    db.execute("UPDATE vault_save_policies SET value=? WHERE vault_id=?", (json.dumps(value), vid))
    assert not vault_save_policy.get(config, db, vid)["publication_ready"]
    return json.dumps(value)


@pytest.mark.parametrize("published", [False, True])
def test_stale_publication_binding_keeps_policy_and_archives_confirmed_note(
    config, ready, published
):
    cid, vid, root, moc, _ = ready
    identity = managed(config, ready)[0] if published else None
    with database.connect(config.state_root) as db:
        stale_binding(config, db, vid)
        before = db.execute(
            "SELECT value FROM vault_save_policies WHERE vault_id=?", (vid,)
        ).fetchone()[0]
        with pytest.raises(InvalidInput, match="placement_active_policy_required"):
            note_placement.context(config, db, cid, vid)
        job = execute(config, db, preview(config, db, cid, vid, "quick"))
        assert job["state"] == "completed", (job.get("error"), job["items"][0])
        assert not (root / "Inbox/传热.md").exists()
        assert note_moc_fields.field((root / "Resource/传热.md").read_text())[2] == [
            "[[Map/热管理 MOC]]"
        ]
        assert "传热" in moc.read_text()
        assert (
            db.execute("SELECT value FROM vault_save_policies WHERE vault_id=?", (vid,)).fetchone()[
                0
            ]
            == before
        )
        with pytest.raises(InvalidInput, match="placement_active_policy_required"):
            note_placement.context(config, db, cid, vid)
        if identity:
            location = publication_locations.get(db, "intake", identity)
            assert location["entry_note_path"] == "Resource/传热.md"
            assert location["package_root"] is None and location["state"] == "published"
            assert location["plan"]["mapping"]["note.md"] == "Resource/传热.md"


def test_archive_after_membership_pause_keeps_completed_relationship_step(config, ready):
    cid, vid, root, _, _ = ready
    managed(config, ready)
    with database.connect(config.state_root) as db:
        stale_binding(config, db, vid)
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        from unittest.mock import patch

        from fiber_bundle.organization import maintenance_archive

        with patch.object(maintenance_archive, "prepare", side_effect=OSError("after membership")):
            job = maintenance.run(config, db, job)
        steps = list(job["items"][0]["steps"])
        assert job["items"][0]["phase"] == "members_done"
        maintenance.action(config, db, job, "resume", None)
        job = maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        assert job["items"][0]["steps"] == steps
        assert (root / "Resource/传热.md").exists()


@pytest.mark.parametrize("kind", ["bundle", "split"])
def test_bundle_does_not_use_flat_note_fallback(config, ready, kind):
    cid, vid, root, _, note = ready
    identity = managed(config, ready)[0]
    with database.connect(config.state_root) as db:
        if kind == "bundle":
            db.execute(
                "UPDATE publication_locations SET package_root='Inbox' WHERE publication_id=?",
                (identity,),
            )
        else:
            location = publication_locations.get(db, "intake", identity)
            location["plan"]["body_files"].append("chapter2.md")
            db.execute(
                "UPDATE publication_locations SET value=? WHERE publication_id=?",
                (json.dumps(location["plan"]), identity),
            )
        stale_binding(config, db, vid)
        job = execute(config, db, preview(config, db, cid, vid, "quick"))
        assert job["state"] == "partial" and "placement_active_policy_required" in job["error"]
        assert note.exists() and not (root / "Resource/传热.md").exists()


def test_map_adoption_still_requires_active_rules(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        db.execute("UPDATE vault_save_policies SET state='draft' WHERE vault_id=?", (vid,))
        with pytest.raises(InvalidInput, match="placement_active_policy_required"):
            moc_registration.adoption_context(config, db, cid, vid)
