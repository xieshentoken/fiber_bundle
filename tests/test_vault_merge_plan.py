"""A merge preview is read-only, complete for managed bodies, and freezes exact current bytes."""

from pathlib import Path

import pytest
from test_article_publication import completed
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate

from fiber_bundle import auth, database, vault_merge_plan
from fiber_bundle.config import InvalidInput
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


def prepared(config, article_scope, policy):
    from fiber_bundle import article_publication

    cid, vid = article_scope[:2]
    run = completed(config, policy, article_scope)
    result = article_publication.publish(config, cid, run)
    pid = result["publication_id"]
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    with database.connect(config.state_root) as db:
        target = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
        auth.set_grant(db, cid, target, query=True, original=True, add=True)
    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    activate(config, target, **settings)
    selection = vault_merge_plan.Selection(
        client_id=cid,
        source_vault_ids=[vid],
        target_vault_id=target,
        target_grants=[vault_merge_plan.Grant(client_id=cid, query=True, original=True, add=True)],
    )
    return cid, vid, target, pid, selection


def fingerprints(config):
    return {
        str(p.relative_to(config.project_root)): p.read_bytes()
        for _, relative in config.vaults
        for p in config.vault_path(relative).rglob("*")
        if p.is_file()
    }


def test_legacy_publication_preview_does_not_change_vaults(config, article_scope, policy):
    cid, vid, target, pid, selection = prepared(config, article_scope, policy)
    before = fingerprints(config)
    with database.connect(config.state_root) as db:
        result = vault_merge_plan.preflight(config, db, selection)
        assert result["ready"], result["conflicts"]
        assert result["files"] and result["inventories"][0]["sources"]
        assert any(
            f["note"] and f["note"]["identity"] == "publication:" + pid for f in result["files"]
        )
        assert not db.execute("SELECT 1 FROM vault_operations WHERE kind='vault_merge'").fetchone()
        frozen = vault_merge_plan.freeze(config, db, selection, result["plan_sha256"])
        assert frozen["state"] == "prepared"
    assert fingerprints(config) == before


def test_external_edit_invalidates_frozen_preview(config, article_scope, policy):
    _, vid, _, _, selection = prepared(config, article_scope, policy)
    with database.connect(config.state_root) as db:
        result = vault_merge_plan.preflight(config, db, selection)
        source = Path(result["inventories"][0]["context"]["root"])
        (source / "historical.md").write_text("# explicit history\n")
        with pytest.raises(InvalidInput, match="preview_changed"):
            vault_merge_plan.freeze(config, db, selection, result["plan_sha256"])


def test_historical_selection_link_and_name_conflicts_are_explicit(config, article_scope, policy):
    _, vid, _, _, selection = prepared(config, article_scope, policy)
    with database.connect(config.state_root) as db:
        result = vault_merge_plan.preflight(config, db, selection)
        source = Path(result["inventories"][0]["context"]["root"])
        (source / "history.md").write_text("# Selected history\n\n[other](other.md)\n")
        (source / "other.md").write_text("# Other\n")
        selection.historical_files = {vid: ["history.md"]}
        result = vault_merge_plan.preflight(config, db, selection)
        assert any(c["reason"] == "linked_file_not_selected" for c in result["conflicts"])
        selection.historical_files[vid].append("other.md")
        result = vault_merge_plan.preflight(config, db, selection)
        assert result["ready"], result["conflicts"]
        target = Path(result["inventories"][-1]["context"]["root"])
        (target / "Inbox" / "Selected history.md").write_text("# Existing\n")
        result = vault_merge_plan.preflight(config, db, selection)
        assert any(c["reason"] == "target_name_conflict" for c in result["conflicts"])
