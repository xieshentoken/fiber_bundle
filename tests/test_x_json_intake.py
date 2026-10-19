"""Nested JSON follows the same automatic/manual admission and deduplication rules."""

import json

import pytest
import test_intake_separated_flow as flow
from test_channel_intake import records
from test_x_article_json import TEXT, article, page, payload

from fiber_bundle import automatic_ingestion, database, vault_save_contract

pytest_plugins = ["test_channel_intake", "test_execution"]


@pytest.mark.parametrize("direct", [False, True])
def test_json_capture_reaches_configured_inbox_only_in_automatic_mode(
    config, entry, policy, monkeypatch, direct
):
    value = article()
    value["content_state"]["blocks"].pop(1)
    value["content_state"]["entity_map"] = []
    value["media_entities"] = []
    value["plain_text"] = TEXT + " 图片之后的段落"
    monkeypatch.setattr(flow, "current_html", lambda: page(payload(value)))
    batch = flow.capture(config, entry, policy, monkeypatch, "basic", direct)
    original = json.loads(records(config, entry)[0]["value"])["receipts"]
    for _ in range(5):
        automatic_ingestion.tick(config)
    row = records(config, entry)[0]
    saved = json.loads(row["value"])
    assert row["batch_id"] == batch and saved["receipts"] == original
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT state FROM automatic_jobs").fetchone()[0] == (
            "complete" if direct else "reviewed"
        )
        before = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        locations = db.execute("SELECT entry_note_path FROM publication_locations").fetchall()
        assert len(locations) == int(direct)
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    if direct:
        relative = locations[0]["entry_note_path"]
        assert relative.startswith(vault_save_contract.unified_settings().inbox_dir + "/")
        note = config.project_root / "test vault/temp_vault1" / relative
        assert note.is_file() and TEXT in note.read_text()
        images = list((config.project_root / "test vault/temp_vault1").rglob("*.png"))
        assert len(images) == 1
        assert images[0].name in note.read_text()
        from test_articles import PNG

        assert images[0].read_bytes() == PNG
        assert saved["links"][0]["publication"]["state"] == "published"
    for _ in range(3):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == before
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == int(direct)
