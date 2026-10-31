"""Compact provenance must be byte-for-byte equivalent to independent source lookup."""

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_supplements import source
from test_unified_directory_roles import enable

from fiber_bundle import (
    auth,
    database,
    note_inventory,
    note_provenance,
    publication_layout,
    search_provenance,
)
from fiber_bundle.inputs import canonical


@pytest.mark.parametrize("processing", ["article", "video", "analysis", "user_revision"])
def test_projection_matches_source_review_and_supplements(config, article_scope, processing):
    enable(config, article_scope)
    cid, vault, receipt = article_scope
    files = source()
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vault, query=True, original=True, add=True)
        row = publication_layout.prepare(config, db, "intake", "synthetic", vault, files)
        row["plan"]["intake_source"] = {
            "source_version_id": receipt["source_version_id"],
            "source_url": "https://example.com/synthetic",
            "review_type": "human",
            "review_actor": "local_admin",
            "processing_kind": "video" if processing == "video" else "article",
            "completeness_status": "not_checked",
            "known_gaps": ["synthetic_gap"],
            "save_basis": "explicit_synthetic_review",
        }
        # Large recovery data is immaterial to the projected source evidence.
        row["plan"]["large_synthetic_evidence"] = "x" * 1_000_000
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id='synthetic'",
            (canonical(row["plan"]),),
        )
        publication_layout.publish(config, db, "intake", "synthetic", cid, files)
        grant = auth.grant(db, cid, vault, "query")
        inventory = note_inventory.read(config, db, grant)
        if processing == "user_revision":
            path = inventory.root / row["entry_note_path"]
            path.write_text(path.read_text() + "\n人工修订\n")
            inventory = note_inventory.read(config, db, grant)
        projected = search_provenance.intake_locations(db, vault)
        assert "large_synthetic_evidence" not in canonical(projected)
        assert "outputs" not in canonical(projected)
        for note in inventory.notes:
            expected = note_provenance.describe(config, db, cid, grant, inventory, note)
            actual = note_provenance.describe(
                config, db, cid, grant, inventory, note, intake_locations=projected
            )
            assert actual == expected
        analysis = next(n for n in inventory.notes if "分析" in n.relative_path)
        assert (
            note_provenance.describe(
                config, db, cid, grant, inventory, analysis, intake_locations=projected
            )["independent_evidence"]
            is False
        )
        # An independent read sees current state; projections never become a cross-request cache.
        db.execute("UPDATE publication_locations SET state='committing' WHERE kind='intake'")
        assert search_provenance.intake_locations(db, vault) == {}
