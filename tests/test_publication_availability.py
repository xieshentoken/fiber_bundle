"""Missing records retain provenance and dedup; explicit recovery restores availability."""

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_note_placement import published

from fiber_bundle import (
    auth,
    database,
    note_inventory,
    publication_availability,
    publication_locations,
    publication_rebind,
)
from fiber_bundle.config import IntegrityError


def test_missing_is_explicit_retains_ledger_and_can_rebind(config, policy, article_scope):
    _, result, note = published(config, policy, article_scope)
    cid, vid = article_scope[:2]
    data = note.read_bytes()
    with database.connect(config.state_root) as db:
        before = publication_locations.get(db, "article", result["publication_id"])
        with pytest.raises(IntegrityError, match="body_still_present"):
            publication_availability.preview(
                config, db, cid, vid, "article", result["publication_id"], 1
            )
        note.unlink()
        with pytest.raises(IntegrityError, match="layout_rebind_required"):
            note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        preview = publication_availability.preview(
            config, db, cid, vid, "article", result["publication_id"], 1
        )
        assert not note.exists()
        publication_availability.apply(config, db, cid, vid, preview["operation_id"])
        after = publication_locations.get(db, "article", result["publication_id"])
        assert {k: v for k, v in after["plan"].items() if k != "availability"} == before["plan"]
        assert after["state"] == "missing"
        assert (
            db.execute(
                "select state from article_publications where id=?", (result["publication_id"],)
            ).fetchone()[0]
            == "published"
        )
        inventory = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        assert all(n.relative_path != before["entry_note_path"] for n in inventory.notes)
        assert (
            publication_availability.apply(config, db, cid, vid, preview["operation_id"])["state"]
            == "missing"
        )
        note.write_bytes(data)
        preview = publication_rebind.preview(
            config, db, cid, vid, "article", result["publication_id"], 2, before["entry_note_path"]
        )
        publication_rebind.apply(config, db, cid, vid, preview["operation_id"])
        assert (
            publication_locations.get(db, "article", result["publication_id"])["state"]
            == "published"
        )
        inventory = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        assert any(n.relative_path == before["entry_note_path"] for n in inventory.notes)
        assert note.read_bytes() == data


def test_file_reappearing_after_missing_preview_rejects(config, policy, article_scope):
    _, result, note = published(config, policy, article_scope)
    raw = note.read_bytes()
    note.unlink()
    with database.connect(config.state_root) as db:
        preview = publication_availability.preview(
            config, db, *article_scope[:2], "article", result["publication_id"], 1
        )
        note.write_bytes(raw)
        with pytest.raises(IntegrityError, match="body_still_present"):
            publication_availability.apply(config, db, *article_scope[:2], preview["operation_id"])
        assert (
            publication_locations.get(db, "article", result["publication_id"])["state"]
            == "published"
        )
