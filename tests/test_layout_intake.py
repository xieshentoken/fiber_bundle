"""Channel review commits gate layout visibility; originals and retained old packages survive."""

from unittest.mock import patch

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, X_URL, current_html
from test_channel_intake import enable, receive
from test_channel_intake import entry as entry
from test_channel_intake_content import grant_original
from test_publication_layout import activate
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    auth,
    database,
    note_inventory,
    note_provenance,
    publication_locations,
    vault_layout_migration,
)
from fiber_bundle.channels import intake_extraction, intake_processing, intake_reviews


def reviewed(config, entry, monkeypatch, http):
    enable(config, entry, fetch_articles=True, download_limit=5)
    batch = receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html; charset=utf-8")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    intake_extraction.advance(config, entry[0]["id"])
    grant_original(config, entry)
    prefix = f"/admin/channels/{entry[0]['id']}/intake/batches/{batch}/links/0"
    packet = http.get(prefix).json()
    review = http.post(
        prefix + "/review",
        json={
            "digest": packet["digest"],
            "reason": "合成来源与图片逐段核对",
            "source_reviewed": True,
            "text_reviewed": True,
            "images_reviewed": True,
        },
    )
    assert review.status_code == 200, review.text
    return batch, review.json()["id"]


def test_intake_preview_and_failed_sidecar_never_expose_partial_note(
    config, entry, monkeypatch, http
):
    batch, review = reviewed(config, entry, monkeypatch, http)
    client, vault = entry[1].client_id, entry[1].vault_id
    activate(config, vault)
    root = config.project_root / "test vault/temp_vault1"
    preview = intake_reviews.publish(config, entry[0]["id"], batch, 0, review, preview_only=True)
    assert not (root / preview["entry_note_path"]).exists()
    original_record = intake_reviews.record
    observations = []

    def fail(side, *args):
        value = args[-1]
        if value["links"][0].get("publication", {}).get("state") == "published":
            with database.connect(config.state_root) as db:
                loc = publication_locations.get(db, "intake", preview["publication_id"])
                observations.append(loc["state"])
                assert not note_inventory.read(
                    config, db, auth.grant(db, client, vault, "query")
                ).notes
            raise OSError("injected sidecar commit failure")
        return original_record(side, *args)

    with patch.object(intake_reviews, "record", side_effect=fail), pytest.raises(OSError):
        intake_reviews.publish(config, entry[0]["id"], batch, 0, review)
    assert observations == ["committing"]
    result = intake_reviews.publish(config, entry[0]["id"], batch, 0, review)
    assert result["entry_note_path"] == preview["entry_note_path"]
    assert list((root / "images").iterdir())[0].read_bytes() == PNG
    with database.connect(config.state_root) as db:
        notes = note_inventory.read(config, db, auth.grant(db, client, vault, "query")).notes
        assert len(notes) == 1 and notes[0].relative_path == result["entry_note_path"]
        inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
        provenance = note_provenance.describe(
            config, db, client, auth.grant(db, client, vault, "query"), inventory, notes[0]
        )
        assert provenance["source_kind"] == "published_channel_content" and provenance["originals"]
    assert intake_reviews.publish(config, entry[0]["id"], batch, 0, review) == result


def test_selected_legacy_intake_conversion_retains_source_and_review(
    config, entry, monkeypatch, http
):
    batch, review = reviewed(config, entry, monkeypatch, http)
    old = intake_reviews.publish(config, entry[0]["id"], batch, 0, review)
    client, vault = entry[1].client_id, entry[1].vault_id
    root = config.project_root / "test vault/temp_vault1"
    original = (root / old["relative_path"] / "note.md").read_bytes()
    activate(config, vault)
    with database.connect(config.state_root) as db:
        packet = vault_layout_migration.preview(config, db, client, vault, "intake", old["id"])
        assert packet["ready"]
        vault_layout_migration.apply(config, db, client, vault, packet["operation_id"])
        notes = note_inventory.read(config, db, auth.grant(db, client, vault, "query")).notes
        assert len(notes) == 1 and notes[0].relative_path.startswith("Inbox/")
    assert (root / old["relative_path"] / "note.md").read_bytes() == original
    current = intake_reviews.publish(config, entry[0]["id"], batch, 0, review)
    assert current["entry_note_path"].startswith("Inbox/")


def test_prepared_channel_sidecar_prevents_discarding_started_publication(
    config, entry, monkeypatch, http
):
    from fiber_bundle import publication_layout
    from fiber_bundle.config import IntegrityError

    batch, review = reviewed(config, entry, monkeypatch, http)
    activate(config, entry[1].vault_id)
    packet = intake_reviews.publish(config, entry[0]["id"], batch, 0, review, preview_only=True)
    with (
        patch.object(publication_layout, "publish", side_effect=OSError("before file commit")),
        pytest.raises(OSError),
    ):
        intake_reviews.publish(config, entry[0]["id"], batch, 0, review)
    with database.connect(config.state_root) as db:
        location = publication_locations.get(db, "intake", packet["publication_id"])
        assert location["state"] == "prepared" and not location["plan"]["claims"]
        with pytest.raises(IntegrityError, match="already_started"):
            publication_layout.discard_preview(config, db, "intake", packet["publication_id"])
        assert publication_locations.get(db, "intake", packet["publication_id"]) is not None
    assert (
        intake_reviews.publish(config, entry[0]["id"], batch, 0, review)["entry_note_path"]
        == packet["entry_note_path"]
    )
