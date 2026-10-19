"""Flat source/analysis publication, safe collisions and frozen legacy compatibility."""

import base64
import json

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_unified_directory_roles import enable

from fiber_bundle import database, publication_layout, publication_note_roles
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest


def source():
    return {
        "note.md": "# Café\n原文\n![](images/a.png)\n".encode(),
        "analysis.md": "# Pi + Skill analysis\n分析局限保持\n".encode(),
        "images/a.png": b"original image",
        "publication-notes.json": publication_note_roles.analysis_manifest(),
    }


@pytest.mark.parametrize("legacy", [False, True])
def test_source_and_analysis_collision_safe_and_linked(config, article_scope, legacy):
    enable(config, article_scope)
    root = config.vault_path("test vault/temp_vault1")
    occupied = {"Inbox/Cafe\u0301.md", "Inbox/CAFÉ-分析.md"}
    for path in occupied:
        (root / path).write_text("human file")
    files = source()
    if legacy:
        files["publication-notes.json"] = canonical(
            {
                "schema": "fiber-document-notes/v1",
                "parts": [{"path": "analysis.md"}],
                "catalog": {"path": "note.md"},
            }
        ).encode()
        files["analysis-provenance.json"] = canonical(
            {
                "schema": "fiber-intake-analysis/v1",
                "source_sha256": digest(files["note.md"]),
                "knowledge_verified": False,
            }
        ).encode()
    with database.connect(config.state_root) as db:
        row = publication_layout.prepare(config, db, "intake", "one", article_scope[1], files)
        assert row["package_root"] is None
        mapping = row["plan"]["mapping"]
        assert mapping["note.md"] != "Inbox/Café.md"
        assert mapping["analysis.md"] != "Inbox/Café-分析.md"
        assert len(set(mapping.values())) == len(files)
        rendered = {
            n: base64.b64decode(row["plan"]["outputs"][p]).decode()
            for n, p in mapping.items()
            if n.endswith(".md")
        }
        from urllib.parse import quote

        assert quote(mapping["analysis.md"].split("/")[-1]) in rendered["note.md"]
        assert quote(mapping["note.md"].split("/")[-1]) in rendered["analysis.md"]
        publication_layout.publish(config, db, "intake", "one", article_scope[0], files)
        published = publication_layout.prepare(config, db, "intake", "one", article_scope[1], files)
        assert published["plan"]["mapping"] == mapping
        assert (
            publication_layout.prepare(
                config, db, "intake", "one", article_scope[1], files, refresh_preview=True
            )
            == published
        )
        # A different article whose title equals the analysis filename cannot overwrite it.
        next_row = publication_layout.prepare(
            config, db, "intake", "two", article_scope[1], {"note.md": "# Café-分析".encode()}
        )
        assert next_row["entry_note_path"] not in mapping.values()
    for path in occupied:
        assert (root / path).read_text() == "human file"


@pytest.mark.parametrize(
    "bad",
    [
        b"null",
        b"[]",
        b"{bad",
        b'{"schema":"unknown"}',
        canonical(
            {
                "schema": publication_note_roles.SUPPLEMENTS_SCHEMA,
                "supplements": [{"path": "../analysis.md", "role": "analysis"}],
            }
        ).encode(),
        canonical(
            {
                "schema": publication_note_roles.SUPPLEMENTS_SCHEMA,
                "supplements": [{"path": "analysis.md", "role": "body"}],
            }
        ).encode(),
        canonical(
            {
                "schema": publication_note_roles.SUPPLEMENTS_SCHEMA,
                "supplements": [{"path": "analysis.md", "role": "analysis"}] * 2,
            }
        ).encode(),
    ],
)
def test_invalid_role_contract_rejected(bad):
    files = source() | {"publication-notes.json": bad}
    with pytest.raises(IntegrityError, match="layout_part_manifest_invalid"):
        publication_note_roles.roles(files)


def test_missing_supplement_rejected_and_unmarked_parts_remain_parts():
    files = source()
    del files["analysis.md"]
    with pytest.raises(IntegrityError):
        publication_note_roles.roles(files)
    files = source()
    files["publication-notes.json"] = json.dumps(
        {
            "schema": "fiber-document-notes/v1",
            "parts": [{"path": "analysis.md"}],
            "catalog": {"path": "note.md"},
        }
    ).encode()
    assert publication_note_roles.roles(files) == ({"note.md", "analysis.md"}, {})


def test_frozen_old_package_is_not_relocated(config, article_scope):
    enable(config, article_scope)
    files = source()
    files["publication-notes.json"] = canonical(
        {
            "schema": "fiber-document-notes/v1",
            "parts": [{"path": "analysis.md"}],
            "catalog": {"path": "note.md"},
        }
    ).encode()
    with database.connect(config.state_root) as db:
        old = publication_layout.prepare(config, db, "intake", "old", article_scope[1], files)
        assert old["package_root"].endswith("-资料包")
        files["publication-notes.json"] = publication_note_roles.analysis_manifest()
        assert (
            publication_layout.prepare(config, db, "intake", "old", article_scope[1], files) == old
        )


def test_two_analyses_keep_distinct_source_names_after_merge(config, article_scope, tmp_path):
    from test_vault_merge_content import merge

    from fiber_bundle import auth, note_inventory, note_provenance, publication_locations

    enable(config, article_scope)
    cid, vault, receipt = article_scope
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vault, query=True, original=True, add=True)
        for title in ("First", "Second"):
            files = source() | {"note.md": ("# " + title + "\n原文\n").encode()}
            row = publication_layout.prepare(config, db, "intake", title, vault, files)
            row["plan"]["intake_source"] = {
                "source_version_id": receipt["source_version_id"],
                "analysis_sha256": digest(files["analysis.md"]),
            }
            db.execute(
                "UPDATE publication_locations SET value=? WHERE publication_id=?",
                (canonical(row["plan"]), title),
            )
            publication_layout.publish(config, db, "intake", title, cid, files)
    target, _, _, _ = merge(config, cid, vault, tmp_path)
    with database.connect(config.state_root) as db:
        grant = auth.grant(db, cid, target, "query")
        inventory = note_inventory.read(config, db, grant)
        for title in ("First", "Second"):
            row = publication_locations.get(db, "intake", title)
            assert row["package_root"] is None
            assert row["plan"]["mapping"]["analysis.md"] == f"Inbox/{title}-分析.md"
            note = next(n for n in inventory.notes if n.relative_path == f"Inbox/{title}-分析.md")
            provenance = note_provenance.describe(config, db, cid, grant, inventory, note)
            assert provenance["source_kind"] == "agent_analysis"
            assert provenance["independent_evidence"] is False
        path = inventory.root / "Inbox/First-分析.md"
        path.write_text(path.read_text() + "\n人工修订\n")
        revised = note_inventory.read(config, db, grant)
        note = next(n for n in revised.notes if n.relative_path == "Inbox/First-分析.md")
        provenance = note_provenance.describe(config, db, cid, grant, revised, note)
        assert provenance["source_kind"] == "user_revision"
        assert provenance["independent_evidence"] is False
        assert provenance["completeness_status"] == "unknown"
