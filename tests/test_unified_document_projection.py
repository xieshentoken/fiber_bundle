"""Real document publisher, lossless review ledger and version-two projected part identities."""

import json
import sqlite3
from unittest.mock import patch

from test_document_parts import chapters, processed
from test_publication_layout import activate
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner

from fiber_bundle import (
    auth,
    document_parts,
    document_publication,
    note_inventory,
    publication_locations,
    search,
    vault_save_contract,
)
from fiber_bundle.inputs import canonical


def test_split_documents_keep_identity_and_review_metadata_in_inbox(http, config, store, owner):
    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, owner[2]["personal"], **settings)
    db, rid, _, _, _ = processed(config, owner, chapters())
    try:
        result = document_publication.publish_locked(config, db, rid)
        assert result["package_root"].startswith("Inbox/")
        parts = document_parts.bindings(
            db, owner[2]["personal"], config.vault_path("test vault/temp_vault1")
        )
        assert len(parts) > 2
        location = publication_locations.get(db, "document", rid)
        location["plan"]["large_test_evidence"] = "synthetic-document" * 100000
        wire = canonical(location["plan"])
        db.execute(
            "UPDATE publication_locations SET value=? WHERE kind='document' AND publication_id=?",
            (wire, rid),
        )
        reads, decodes = [], []
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

        db.row_factory = row
        with patch.object(json, "loads", decode):
            notes = note_inventory.read(
                config, db, auth.grant(db, owner[0], owner[2]["personal"], "query")
            )
        db.row_factory = sqlite3.Row
        assert reads == decodes == [len(wire)]
        projection = publication_locations.project_inventory(db, owner[2]["personal"])
        assert (
            document_parts.bindings(
                db,
                owner[2]["personal"],
                config.vault_path("test vault/temp_vault1"),
                body_projections=projection.document_projections,
            )
            == parts
        )
        assert {p.note_id for p in parts.values()} <= {n.identity for n in notes.notes}
        hit = search.vault_search(config, db, owner[0], "chapter-token-7", [owner[2]["personal"]])[
            "results"
        ][0]
        assert hit["source_kind"] == "published_document"
        assert hit["document_part"]["controlled_layout_projection"]
        assert hit["document_part"]["matches_reviewed_extraction"]
        assert (
            hit["document_part"]["original_part_sha256"]
            == hit["document_part"]["published_part_sha256"]
        )
        assert not any(n.relative_path.startswith("Attachments/") for n in notes.notes)
    finally:
        db.close()
