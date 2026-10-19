"""Isolated real filesystem publication and recovery under explicit policies."""

from unittest.mock import patch

import pytest
from test_article_publication import completed, destination
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy

from fiber_bundle import (
    article_publication,
    auth,
    database,
    note_inventory,
    note_provenance,
    publication_commit,
    publication_layout,
    publication_locations,
    vault_save_policy,
)
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_save_contract import SaveSettings


def activate(config, vault, **kwargs):
    with database.connect(config.state_root) as db:
        current = vault_save_policy.get(config, db, vault)
        preview = vault_save_policy.preview(
            config,
            db,
            vault,
            SaveSettings(layout_mode="obsidian_folders", **kwargs),
            current["revision"],
            enable_publication=True,
        )
        result = vault_save_policy.save(
            config, db, vault, preview["preview_id"], current["revision"]
        )
        assert result["state"] == "active" and result["publication_ready"]
        return result


def test_article_flat_images_inventory_provenance_and_human_edits(config, policy, article_scope):
    activate(config, article_scope[1])
    rid = completed(config, policy, article_scope)
    result = article_publication.publish(config, article_scope[0], rid)
    note = destination(config, result)
    assert note.parent.name == "Inbox" and note.name != "note.md"
    assert " ![[" in " " + note.read_text() or "![[" in note.read_text()
    assert len(list((note.parent.parent / "images").iterdir())) == 1
    assert not list(note.parent.parent.glob("article-*"))
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE client_vault_grants SET can_query=1,can_original=1 WHERE "
            "client_id=? AND vault_id=?",
            article_scope[:2],
        )
        inv = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        current = next(n for n in inv.notes if n.relative_path == result["note_path"])
        assert current.publication_id == result["publication_id"]
        assert current.identity == "publication:" + result["publication_id"]
        provenance = note_provenance.describe(
            config, db, article_scope[0], auth.grant(db, *article_scope[:2], "query"), inv, current
        )
        assert provenance["source_kind"] == "published_article" and provenance["originals"]
    note.write_text(note.read_text() + "\n人工补充\n")
    assert (
        article_publication.publish(config, article_scope[0], rid)["note_path"]
        == result["note_path"]
    )
    assert note.read_text().endswith("人工补充\n")


def test_collision_keeps_existing_unicode_alias(config, article_scope):
    activate(config, article_scope[1])
    root = config.project_root / "test vault/temp_vault1"
    (root / "Inbox/Ａ标题.md").write_text("keep")
    (root / "Inbox/标题.md").write_text("keep")
    with database.connect(config.state_root) as db:
        row = publication_layout.prepare(
            config, db, "intake", "one", article_scope[1], {"note.md": "# 标题\n正文".encode()}
        )
        assert row["entry_note_path"] != "Inbox/标题.md"
        publication_layout.publish(
            config, db, "intake", "one", article_scope[0], {"note.md": "# 标题\n正文".encode()}
        )
    assert (root / "Inbox/标题.md").read_text() == "keep"


def test_crash_after_asset_write_retries_frozen_paths_without_overwrite(config, article_scope):
    activate(config, article_scope[1], image_link_style="markdown")
    files = {"note.md": b"# Test\n![](images/a.png)\n", "images/a.png": b"image"}
    with database.connect(config.state_root) as db:
        row = publication_layout.prepare(config, db, "intake", "resume", article_scope[1], files)
        original = publication_commit.save

        def fail(db, row, state):
            original(db, row, state)
            if state == "committing" and row["plan"]["file_keys"]:
                raise OSError("injected")

        with patch.object(publication_commit, "save", side_effect=fail), pytest.raises(OSError):
            publication_layout.publish(config, db, "intake", "resume", article_scope[0], files)
        assert publication_locations.get(db, "intake", "resume")["state"] == "reconcile"
        publication_layout.publish(config, db, "intake", "resume", article_scope[0], files)
        final = publication_locations.get(db, "intake", "resume")
        assert final["entry_note_path"] == row["entry_note_path"] and final["state"] == "published"
        assert len(final["plan"]["claims"]) == 2


def test_changed_claimed_asset_blocks_resume(config, article_scope):
    activate(config, article_scope[1])
    files = {"note.md": b"# Test\n", "images/a.png": b"image"}
    with database.connect(config.state_root) as db:
        publication_layout.prepare(config, db, "intake", "changed", article_scope[1], files)
        original = publication_commit.save

        def fail(db, row, state):
            original(db, row, state)
            if state == "committing" and row["plan"]["file_keys"]:
                raise OSError("injected")

        with patch.object(publication_commit, "save", side_effect=fail), pytest.raises(OSError):
            publication_layout.publish(config, db, "intake", "changed", article_scope[0], files)
        row = publication_locations.get(db, "intake", "changed")
        image = next(p for p in row["plan"]["outputs"] if p.endswith(".png"))
        (config.project_root / "test vault/temp_vault1" / image).write_bytes(b"manual")
        with pytest.raises(IntegrityError):
            publication_layout.publish(config, db, "intake", "changed", article_scope[0], files)
        assert publication_locations.get(db, "intake", "changed")["state"] == "reconcile"
