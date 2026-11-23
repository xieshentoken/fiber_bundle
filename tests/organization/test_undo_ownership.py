"""A delayed compensation seal must not adopt concurrent intake writes."""

import base64
from dataclasses import asdict, replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_jobs import ready as ready
from test_maintenance_undo import start
from test_note_moc_locations import managed

from fiber_bundle import database, moc_store, note_identity_store, publication_file_io
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import maintenance, maintenance_undo, store


def publish_unrelated(config, db, ready):
    cid, vid, root, *_ = ready
    path = "Inbox/并发入库.md"
    (root / path).write_text("# 新笔记\n独立入库正文。\n")
    raw, key = publication_file_io.read(root, path)
    publication = str(uuid4())
    info = root.stat()
    identity = note_identity_store.Registration(
        "note:" + str(uuid4()),
        publication,
        path,
        digest(raw),
        tuple(key),
        (info.st_dev, info.st_ino),
        1,
    )
    note_identity_store.save(config, vid, cid, identity)
    plan = {
        "mapping": {"note.md": path},
        "outputs": {path: base64.b64encode(raw).decode()},
        "published_hashes": {path: digest(raw)},
        "file_keys": {path: key},
    }
    db.execute(
        "INSERT INTO publication_locations VALUES('intake',?,?,?,NULL,1,'published',?)",
        (publication, vid, path, canonical(plan)),
    )
    location = dict(
        db.execute(
            "SELECT * FROM publication_locations WHERE publication_id=?", (publication,)
        ).fetchone()
    )
    operation = str(uuid4())
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'completed','{}',0,0)",
        (operation, vid, "unrelated_intake"),
    )
    decision = {"entity": identity.identity, "moc": "other", "state": "pinned"}
    store.put(config, "decisions", "late-decision", vid, decision)
    other_moc = str(uuid4())
    record = moc_store.Record("Other Map", info.st_dev, info.st_ino, 1, {})
    moc_store.save(config, vid, other_moc, cid, record, "adopt-existing", 0)
    return identity, location, operation, decision, other_moc, record, raw


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("managed_note", [False, True])
def test_delayed_seal_cancel_preserves_concurrent_intake_and_owned_archive(
    config, ready, legacy, managed_note
):
    cid, vid, root, moc, note = ready
    if managed_note:
        managed(config, ready)
    before = {p: (p.read_bytes(), p.stat().st_ino) for p in (moc, note)}
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        first = next(i for i in job["items"] if i["path"] == "Inbox/传热.md")
        job["items"] = [first] + [i for i in job["items"] if i is not first]
        store.put(config, "jobs", job["id"], vid, job)
        with patch.object(maintenance_undo, "seal", side_effect=SystemExit("before seal")):
            with pytest.raises(SystemExit):
                maintenance.run(config, db, job)
        journal = store.get(config, "maintenance_undo", job["id"])
        assert journal["state"] == "applying"
        if legacy:
            journal.pop("unit_paths")
            store.put(config, "maintenance_undo", job["id"], vid, journal)
        identity, location, operation, decision, other, record, raw = publish_unrelated(
            config, db, ready
        )
        job = store.get(config, "jobs", job["id"])
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "cancelled", result
        assert identity in note_identity_store.records(config, vid)
        assert (
            dict(
                db.execute(
                    "SELECT * FROM publication_locations WHERE publication_id=?",
                    (identity.publication_id,),
                ).fetchone()
            )
            == location
        )
        assert (
            db.execute("SELECT state FROM vault_operations WHERE id=?", (operation,)).fetchone()[0]
            == "completed"
        )
        assert store.get(config, "decisions", "late-decision") == decision
        assert moc_store.load(config, vid, other) == record
        assert (root / identity.relative_path).read_bytes() == raw
        assert not (root / first["target"]).exists()
        assert all(p.read_bytes() == b and p.stat().st_ino == ino for p, (b, ino) in before.items())


@pytest.mark.parametrize("same_entity", [False, True])
def test_unknown_member_on_owned_moc_conflicts_before_any_file_rollback(config, ready, same_entity):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        with patch.object(maintenance_undo, "seal", side_effect=SystemExit("before seal")):
            with pytest.raises(SystemExit):
                maintenance.run(config, db, job)
        job = store.get(config, "jobs", job["id"])
        mid = job["items"][0]["add"][0]
        current = moc_store.load(config, vid, mid)
        entity = job["items"][0]["entity"] if same_entity else "note:" + str(uuid4())
        later = replace(
            current,
            revision=current.revision + 1,
            members={**current.members, entity: ["manual"]},
        )
        moc_store.save(config, vid, mid, cid, later, "organization-link", current.revision)
        before = {p: p.read_bytes() for p in root.rglob("*.md")}
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "rollback_conflict", result
        assert result["error"] == "maintenance_undo_relationship_conflict"
        assert asdict(moc_store.load(config, vid, mid)) == asdict(later)
        assert all(p.read_bytes() == raw for p, raw in before.items())


def test_already_sealed_legacy_journal_does_not_delete_unowned_rows(config, ready):
    _, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = maintenance.run(config, db, start(config, db, ready))
        identity, location, operation, decision, other, record, raw = publish_unrelated(
            config, db, ready
        )
        journal = store.get(config, "maintenance_undo", job["id"])
        # Reproduce the old whole-vault sealing behavior, including its mistaken
        # claim to a concurrent intake operation. No real data is involved.
        journal["after_state"] = maintenance_undo.state.snapshot(
            config, db, vid, persist=True, compact=True
        )
        journal["operations"].append(operation)
        journal.pop("unit_paths")
        store.put(config, "maintenance_undo", job["id"], vid, journal)
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "cancelled", result
        assert identity in note_identity_store.records(config, vid)
        assert (
            dict(
                db.execute(
                    "SELECT * FROM publication_locations WHERE publication_id=?",
                    (identity.publication_id,),
                ).fetchone()
            )
            == location
        )
        assert (
            db.execute("SELECT state FROM vault_operations WHERE id=?", (operation,)).fetchone()[0]
            == "completed"
        )
        assert store.get(config, "decisions", "late-decision") == decision
        assert moc_store.load(config, vid, other) == record
        assert (root / identity.relative_path).read_bytes() == raw


def test_unknown_incomplete_registration_is_preserved_without_claiming_cancel(config, ready):
    _, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = maintenance.run(config, db, start(config, db, ready))
        before = {p: p.read_bytes() for p in root.rglob("*.md")}
        operation = str(uuid4())
        value = {"plan_id": None, "path": job["mocs"][0]["path"]}
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,'moc_registration','applying',?,0,0)",
            (operation, vid, canonical(value)),
        )
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "rollback_conflict", result
        assert result["error"] == "maintenance_undo_relationship_conflict"
        assert (
            db.execute("SELECT state FROM vault_operations WHERE id=?", (operation,)).fetchone()[0]
            == "applying"
        )
        assert all(p.read_bytes() == raw for p, raw in before.items())
