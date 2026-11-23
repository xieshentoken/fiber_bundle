"""Archive reads only selected full payloads while keeping all mapped members visible."""

from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_note_moc_locations import managed

from fiber_bundle import database, publication_locations
from fiber_bundle import publication_location_selection as select
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical


def test_all_mapping_members_select_exact_rows_and_preserve_large_unrelated_payload(config, ready):
    _, vault, *_ = ready
    identity, _, plan = managed(config, ready)
    with database.connect(config.state_root) as db:
        wire = canonical({**plan, "large_retained_evidence": "synthetic" * 100000})
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?", (wire, identity)
        )
        other = {
            **plan,
            "mapping": {"entry.md": "Resource/other.md", "asset.png": "Attachments/a.png"},
        }
        db.execute(
            "INSERT INTO publication_locations "
            "VALUES('intake','other',?,'Resource/other.md',NULL,1,'published',?)",
            (vault, canonical(other)),
        )
        original = publication_locations.get
        with patch.object(publication_locations, "get", wraps=original) as reads:
            assert select.at_path(db, vault, "absent.md") == []
            assert select.affected(db, vault, {"unrelated.md"}) == []
            assert reads.call_count == 0
            assert [
                r["publication_id"] for r in select.affected(db, vault, {"Attachments/a.png"})
            ] == ["other"]
            assert reads.call_count == 1
            assert [r["publication_id"] for r in select.at_path(db, vault, "Inbox/传热.md")] == [
                identity
            ]
            assert reads.call_count == 2
        assert (
            db.execute(
                "SELECT value FROM publication_locations WHERE publication_id=?", (identity,)
            ).fetchone()[0]
            == wire
        )
        other["mapping"]["asset.png"] = "Attachments/b.png"
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id='other'",
            (canonical(other),),
        )
        assert select.affected(db, vault, {"Attachments/a.png"}) == []
        assert [r["publication_id"] for r in select.affected(db, vault, {"Attachments/b.png"})] == [
            "other"
        ]
        assert select.affected(db, "another-vault", {"Attachments/b.png"}) == []


def test_package_root_and_malformed_mapping_keep_selection_guards(config, ready):
    _, vault, *_ = ready
    identity, _, plan = managed(config, ready)
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE publication_locations SET package_root='Book/expert' WHERE publication_id=?",
            (identity,),
        )
        assert len(select.at_path(db, vault, "Book/expert")) == 1
        plan["mapping"] = []
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        with pytest.raises(IntegrityError, match="mapping invalid"):
            select.affected(db, vault, {"unrelated.md"})
