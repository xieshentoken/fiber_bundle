"""Real document publisher, lossless review ledger and version-two projected part identities."""

from test_document_parts import chapters, processed
from test_publication_layout import activate
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner

from fiber_bundle import (
    auth,
    document_parts,
    document_publication,
    note_inventory,
    search,
    vault_save_contract,
)


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
        notes = note_inventory.read(
            config, db, auth.grant(db, owner[0], owner[2]["personal"], "query")
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
