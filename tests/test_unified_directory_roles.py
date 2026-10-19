"""Observable version compatibility, shared attachment placement and immutable originals."""

import base64

import pytest
from pydantic import ValidationError
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate

from fiber_bundle import (
    attachment_projection,
    auth,
    database,
    note_inventory,
    publication_layout,
    vault_save_policy,
)
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import AssetInput, canonical
from fiber_bundle.vault_save_contract import (
    SaveSettings,
    SettingsError,
    archive_mode,
    settings_wire,
    unified_settings,
)


def enable(config, scope):
    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    return activate(config, scope[1], **settings)


def test_legacy_wire_and_preview_mode_are_unchanged():
    old = {
        "layout_mode": "legacy_bundle",
        "inbox_dir": "Inbox",
        "archive_dir": "Archive",
        "image_dir": "images",
        "image_link_style": "obsidian",
        "archive_on_moc": False,
        "moc_destinations": {},
    }
    assert canonical(settings_wire(SaveSettings.model_validate(old))) == canonical(old)
    assert archive_mode(old | {"archive_on_moc": True}) == "preview"
    assert archive_mode(settings_wire(unified_settings())) == "off"
    with pytest.raises(ValidationError):
        SaveSettings(book_dir="Elsewhere")
    with pytest.raises(ValidationError):
        SaveSettings(policy_version=2, archive_on_moc=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"book_dir": "Inbox/books"},
        {"moc_dir": "Resource"},
        {"attachment_root": "Inbox/assets"},
        {"image_dir": "Attachments/pristin/pictures"},
        {"original_dir": "Elsewhere"},
        {"image_dir": "Attachments"},
        {"book_dir": "inbox/books"},
        {"inbox_dir": "Café", "archive_dir": "Cafe\u0301"},
    ],
)
def test_version_two_rejects_overlapping_roles(config, article_scope, changes):
    with database.connect(config.state_root) as db, pytest.raises(SettingsError):
        value = settings_wire(unified_settings()) | changes
        vault_save_policy.preview(
            config, db, article_scope[1], SaveSettings.model_validate(value), 0
        )


def test_explicit_preset_preview_and_book_preserves_package_bytes(config, article_scope):
    root = config.project_root / "test vault/temp_vault1"
    with database.connect(config.state_root) as db:
        preview = vault_save_policy.preview(
            config, db, article_scope[1], unified_settings(), 0, enable_publication=True
        )
        assert preview["paths"]["expert_package"].startswith("Book/")
        assert not (root / "Map").exists()
        vault_save_policy.save(config, db, article_scope[1], preview["preview_id"], 0)
        files = {
            "note.md": b"# Draft\n",
            "concepts/one.md": b"![](../images/1.png)\n",
            "images/1.png": b"image",
            ".fiber-expert-canonical/evidence.json": b"{}",
        }
        row = publication_layout.prepare(config, db, "expert", "draft", article_scope[1], files)
        assert row["package_root"].startswith("Book/")
        assert all(
            base64.b64decode(row["plan"]["outputs"][path]) == files[n]
            for n, path in row["plan"]["mapping"].items()
        )


def test_ordinary_file_and_image_links_externalized_without_overwriting(config, article_scope):
    enable(config, article_scope)
    files = {
        "note.md": (
            "# 同名资料\n[规格](files/a%20b.pdf#p2)\n![图](images/a.png)\n"
            "[ref]: files/a%20b.pdf\n`[示例](files/a%20b.pdf)`\n"
        ).encode(),
        "files/a b.pdf": b"%PDF-original",
        "images/a.png": b"image",
    }
    with database.connect(config.state_root) as db:
        row = publication_layout.prepare(config, db, "intake", "first", article_scope[1], files)
        assert row["package_root"] is None
        assert row["plan"]["mapping"]["files/a b.pdf"].startswith("Attachments/pristin/")
        assert row["plan"]["mapping"]["images/a.png"].startswith("Attachments/images/")
        publication_layout.publish(config, db, "intake", "first", article_scope[0], files)
        root = config.project_root / "test vault/temp_vault1"
        text = (root / row["entry_note_path"]).read_text()
        assert "../Attachments/pristin/" in text and "#p2" in text
        assert "`[示例](files/a%20b.pdf)`" in text
        second = publication_layout.prepare(config, db, "intake", "second", article_scope[1], files)
        assert second["entry_note_path"] != row["entry_note_path"]


def test_original_projection_excluded_from_note_index_and_manual_edits_preserved(
    config, store, article_scope
):
    enable(config, article_scope)
    receipt = store.ingest(
        ingress_scope="synthetic",
        event_id="uv-original",
        vault="personal",
        source_key="uv-original",
        raw_event=b"event",
        items=(AssetInput(b"# Raw original\n", "original.md", "text/markdown"),),
        session_scope="synthetic",
        session_id="uv",
        retention="knowledge",
    )
    with database.connect(config.state_root) as db:
        auth.set_grant(db, article_scope[0], article_scope[1], query=True, original=True, add=True)
        asset = db.execute(
            "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt.source_version_id,)
        ).fetchone()[0]
        result = attachment_projection.project(
            config, db, *article_scope[:2], receipt.source_version_id, asset
        )
        assert result["path"].startswith("Attachments/pristin/")
        again = attachment_projection.project(
            config, db, *article_scope[:2], receipt.source_version_id, asset
        )
        assert again["path"] == result["path"]
        inventory = note_inventory.read(config, db, auth.grant(db, *article_scope[:2], "query"))
        assert not any(n.relative_path == result["path"] for n in inventory.notes)
        copy = inventory.root / result["path"]
        copy.write_bytes(b"human replacement")
        with pytest.raises(IntegrityError):
            attachment_projection.project(
                config, db, *article_scope[:2], receipt.source_version_id, asset
            )
        assert copy.read_bytes() == b"human replacement"
        assert store.read_asset(asset, vault="personal") == b"# Raw original\n"
        auth.set_grant(db, article_scope[0], article_scope[1], query=True, original=False, add=True)
        with pytest.raises(auth.Denied):
            attachment_projection.project(
                config, db, *article_scope[:2], receipt.source_version_id, asset
            )


def test_projected_markdown_limit_checked_after_link_expansion(config, article_scope):
    from fiber_bundle.document_split import SplitRequired

    enable(config, article_scope)
    files = {"note.md": b"# Link\n[x](a.pdf)\n", "a.pdf": b"%PDF"}
    with database.connect(config.state_root) as db:
        with pytest.raises(SplitRequired, match="document_split_unit_limit"):
            publication_layout.prepare(
                config,
                db,
                "intake",
                "expanded",
                article_scope[1],
                files,
                markdown_limits={
                    "markdown_bytes": len(files["note.md"]),
                    "markdown_total_bytes": 10000,
                },
            )
        assert not db.execute(
            "SELECT 1 FROM publication_locations WHERE publication_id='expanded'"
        ).fetchone()
        assert not list((config.project_root / "test vault/temp_vault1/Inbox").iterdir())


def test_explicit_original_copy_recovery_rejects_user_changes(config, store, article_scope):
    from unittest.mock import patch

    from fiber_bundle import attachment_recovery, publication_file_io

    enable(config, article_scope)
    receipt = store.ingest(
        ingress_scope="synthetic",
        event_id="copy-recovery",
        vault="personal",
        source_key="copy-recovery",
        raw_event=b"event",
        items=(AssetInput(b"preserved-original-bytes", "source.pdf", "application/pdf"),),
        session_scope="synthetic",
        session_id="recovery",
        retention="knowledge",
    )
    with database.connect(config.state_root) as db:
        auth.set_grant(db, *article_scope[:2], query=True, original=True, add=True)
        asset = db.execute(
            "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt.source_version_id,)
        ).fetchone()[0]
        with (
            patch.object(publication_file_io, "read", side_effect=OSError("injected-after-write")),
            pytest.raises(OSError),
        ):
            attachment_projection.project(
                config, db, *article_scope[:2], receipt.source_version_id, asset
            )
        item = attachment_recovery.listing(db, article_scope[1])[0]
        assert item["state"] == "reconcile"
        recovered = attachment_recovery.resume(config, db, article_scope[1], item["id"])
        assert recovered["state"] == "projected"
        target = config.vault_path("test vault/temp_vault1") / recovered["path"]
        target.write_bytes(b"human-edit")
        with pytest.raises(IntegrityError):
            attachment_projection.project(
                config, db, *article_scope[:2], receipt.source_version_id, asset
            )
        with pytest.raises(IntegrityError):
            attachment_recovery.resume(config, db, article_scope[1], item["id"])
        assert target.read_bytes() == b"human-edit"
