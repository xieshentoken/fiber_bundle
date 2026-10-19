"""Persistent cancellation precedes execution and preserves already committed notes."""

import asyncio
import json

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, X_URL, current_html
from test_channel_intake import enable, receive, records
from test_intake_abort_commands import command
from test_intake_separated_flow import capture

from fiber_bundle import (
    automatic_ingestion,
    database,
    feishu_receiver,
    intake_abort_commands,
    weixin_inbox,
)
from fiber_bundle.channels import intake_extraction, intake_processing, intake_reviews
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake", "test_execution"]


def test_queued_batch_abort_survives_receiver_restart_without_archiving(config, entry):
    enable(config, entry, policy_version=2)
    batch = receive(config, entry)
    command(config, entry, "abort " + batch)
    feishu_receiver.initialize(config, entry[1])
    feishu_receiver.advance(config, entry[1])
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    with weixin_inbox.connection(config, entry[1]) as db:
        assert (
            db.execute("SELECT state FROM wx_batches WHERE id=?", (batch,)).fetchone()[0]
            == "cancelled"
        )
        assert (
            db.execute("SELECT state FROM fiber_intake_abort_commands").fetchone()[0] == "stopped"
        )
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_abort_task_number_blocks_publish_before_stop_tick(config, entry, policy, monkeypatch):
    batch = capture(config, entry, policy, monkeypatch, "pi_skill", True)
    with database.connect(config.state_root) as db:
        identity = db.execute("SELECT id FROM automatic_jobs").fetchone()[0]
    command(config, entry, "abort " + identity)
    assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert not db.execute("SELECT 1 FROM publication_locations").fetchone()
    feishu_receiver.advance(config, entry[1])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "cancelled" and link["aborted"]
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT state FROM automatic_jobs").fetchone()[0] == "cancelled"
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    with pytest.raises(InvalidInput, match="aborted"):
        intake_extraction.retry(
            config, entry[0]["id"], batch, 0, records(config, entry)[0]["revision"]
        )


def test_already_published_abort_does_not_delete_and_receipt_is_sent_once(
    config, entry, policy, monkeypatch
):
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    for _ in range(3):
        automatic_ingestion.tick(config)
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    path = config.vault_path("test vault/temp_vault1") / link["publication"]["entry_note_path"]
    before = path.read_bytes()
    command(config, entry, "abort " + batch)
    feishu_receiver.advance(config, entry[1])
    sent = []

    async def sender(owner, body, guard):
        guard()
        sent.append((owner, body))
        return True

    asyncio.run(intake_abort_commands.deliver(config, entry[1], sender, lambda: None))
    command(config, entry, "abort " + batch)
    asyncio.run(intake_abort_commands.deliver(config, entry[1], sender, lambda: None))
    assert len(sent) == 1 and sent[0][0] == entry[1].owner_id
    assert "已提交入库" in sent[0][1]
    assert path.read_bytes() == before
    with weixin_inbox.connection(config, entry[1]) as db:
        assert db.execute("SELECT state,receipt_state FROM fiber_intake_abort_commands").fetchone()[
            :
        ] == ("too_late", "sent")


def test_unknown_abort_delivery_is_not_automatically_retried(config, entry):
    enable(config, entry, policy_version=2)
    command(config, entry, "abort 1234abcd")
    sent = []

    async def sender(*args):
        sent.append(args)
        return False

    for _ in range(2):
        asyncio.run(intake_abort_commands.deliver(config, entry[1], sender, lambda: None))
    assert len(sent) == 1
    with weixin_inbox.connection(config, entry[1]) as db:
        assert (
            db.execute("SELECT receipt_state FROM fiber_intake_abort_commands").fetchone()[0]
            == "unknown"
        )


def test_specific_task_cancellation_preserves_other_batch(config, entry, policy, monkeypatch):
    first = capture(config, entry, policy, monkeypatch, "basic", True)
    second = receive(config, entry, text=X_URL, identity="second-source")
    intake_processing.advance(config, entry[0]["id"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    intake_extraction.advance(config, entry[0]["id"])
    with database.connect(config.state_root) as db:
        jobs = db.execute("SELECT id,value FROM automatic_jobs").fetchall()
        identity = next(j["id"] for j in jobs if json.loads(j["value"])["intake"]["batch"] == first)
    command(config, entry, "abort " + identity[:8])
    feishu_receiver.advance(config, entry[1])
    for _ in range(6):
        automatic_ingestion.tick(config)
    links = {r["batch_id"]: json.loads(r["value"])["links"][0] for r in records(config, entry)}
    assert links[first]["aborted"] and not links[first].get("publication")
    assert links[second]["publication"]["state"] == "published"


def test_abort_blocks_previously_approved_manual_publication(config, entry, policy, monkeypatch):
    from fiber_bundle.inputs import canonical
    from fiber_bundle.objects import digest

    batch = capture(config, entry, policy, monkeypatch, "basic", False)
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    review = intake_reviews.review(
        config, entry[0]["id"], batch, 0, digest(canonical(link["files"]).encode()), "Checked"
    )
    command(config, entry, "abort " + batch)
    with pytest.raises(InvalidInput, match="aborted"):
        intake_reviews.publish(config, entry[0]["id"], batch, 0, review["id"])
    assert not link.get("publication")
