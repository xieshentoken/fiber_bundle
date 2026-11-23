"""Fresh inventories project compact evidence and retain visibility protections."""

import json
import sqlite3
from unittest.mock import patch

from test_jobs import ready as ready
from test_note_moc_locations import managed

from fiber_bundle import (
    attachment_projection,
    auth,
    database,
    note_inventory,
    publication_locations,
)
from fiber_bundle.inputs import canonical


def test_inventory_keeps_large_location_in_sqlite_and_retained_sources_hidden(config, ready):
    cid, vid, root, _, note = ready
    identity, _, plan = managed(config, ready)
    plan["large_test_evidence"] = "large-synthetic-location" * 100000
    plan["retained_source_root"] = "Retained"
    retained = root / "Retained/old.md"
    retained.parent.mkdir()
    retained.write_text("# Retained source must stay hidden\n")
    wire = canonical(plan)
    reads = []
    decodes = []
    before = {path: path.read_bytes() for path in (note, retained)}
    original_loads = json.loads

    def decode(value, *args, **kwargs):
        if value == wire:
            decodes.append(len(value))
        return original_loads(value, *args, **kwargs)

    def row(cursor, values):
        result = sqlite3.Row(cursor, values)
        if "value" in result.keys() and result["value"] == wire:
            reads.append(len(result["value"]))
        return result

    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?", (wire, identity)
        )
        vault = auth.grant(db, cid, vid, "query")
        db.row_factory = row
        with patch.object(publication_locations.json, "loads", decode):
            inventory = note_inventory.read(config, db, vault)
        assert {n.relative_path for n in inventory.notes} == {
            "Inbox/传热.md",
            "Map/热管理 MOC.md",
        }
        actual = next(n for n in inventory.notes if n.relative_path == "Inbox/传热.md")
        assert actual.identity == publication_locations.note_id("intake", identity)
        assert reads == [] and decodes == []  # No full recovery/output payload crosses SQLite.
        assert (
            db.execute(
                "SELECT value FROM publication_locations WHERE publication_id=?", (identity,)
            ).fetchone()[0]
            == wire
        )
    assert {path: path.read_bytes() for path in before} == before


def test_next_inventory_observes_value_change_without_revision_increment(config, ready):
    cid, vid, _, _, _ = ready
    identity, _, plan = managed(config, ready)
    with database.connect(config.state_root) as db:
        vault = auth.grant(db, cid, vid, "query")
        assert "Inbox/传热.md" in {
            n.relative_path for n in note_inventory.read(config, db, vault).notes
        }
        plan["body_files"] = []
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        assert "Inbox/传热.md" not in {
            n.relative_path for n in note_inventory.read(config, db, vault).notes
        }
        assert (
            db.execute(
                "SELECT revision FROM publication_locations WHERE publication_id=?", (identity,)
            ).fetchone()[0]
            == 1
        )


def test_uncommitted_package_root_stays_hidden(config, ready):
    cid, vid, root, _, _ = ready
    identity, _, plan = managed(config, ready)
    private = root / "Book/pending-package/unmapped.md"
    private.parent.mkdir(parents=True)
    private.write_text("# Uncommitted package member\n")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE publication_locations SET state='prepared',package_root=? "
            "WHERE publication_id=?",
            ("Book/pending-package", identity),
        )
        result = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        assert all(
            n.relative_path not in {"Inbox/传热.md", "Book/pending-package/unmapped.md"}
            for n in result.notes
        )


def test_location_projection_keeps_current_and_historical_attachment_roots(config, ready):
    cid, vid, root, _, _ = ready
    identity, _, plan = managed(config, ready)
    plan["policy_binding"]["settings"]["attachment_root"] = "Old Attachments"
    for folder in ("Attachments", "Old Attachments", "Earlier Attachments"):
        path = root / folder / "private.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text("# Attachment stays hidden\n")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'published',?,0,0)",
            (
                "historical-attachment",
                vid,
                "attachment_projection",
                canonical({"path": "Earlier Attachments/original.pdf"}),
            ),
        )
        projection = publication_locations.project_inventory(db, vid, include_attachments=True)
        assert (
            attachment_projection.hidden_roots(db, vid)
            == attachment_projection.hidden_roots(
                db, vid, location_roots=projection.attachment_roots
            )
            == {"Attachments", "Old Attachments", "Earlier Attachments"}
        )
        inventory = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        assert {note.relative_path for note in inventory.notes} == {
            "Inbox/传热.md",
            "Map/热管理 MOC.md",
        }
