"""Receipts describe actual stages, survive restart and never repeat unknown sends."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_channel_intake import enable, records
from test_intake_separated_flow import capture

from fiber_bundle import automatic_ingestion, automatic_jobs, database
from fiber_bundle.channels import intake_extraction, intake_receipts
from fiber_bundle.channels import store as channels

pytest_plugins = ["test_channel_intake", "test_execution"]


@pytest.fixture
def notify(config, entry, monkeypatch):
    enable(config, entry, receipt=True)
    clock = [time.time()]
    monkeypatch.setattr(intake_receipts, "time", SimpleNamespace(time=lambda: clock[0]))
    calls = []

    def sender(owner, body, guard):
        guard()
        calls.append(body)
        return {"category": "confirmed"}

    adapter = SimpleNamespace(_fiber_send_receipt=sender)

    def deliver(times=1):
        for _ in range(times):
            clock[0] += 6
            asyncio.run(intake_receipts.deliver(config, entry[0]["id"], adapter))
        return calls

    return calls, adapter, deliver


def test_received_then_actual_saved_notification_once(config, entry, policy, monkeypatch, notify):
    calls, _, deliver = notify
    capture(config, entry, policy, monkeypatch, "basic", True)
    deliver()
    assert len(calls) == 1 and "消息已接收" in calls[0]
    assert "笔记已保存入库" not in calls[0]
    for _ in range(3):
        automatic_ingestion.tick(config)
    deliver(5)
    assert len(calls) == 2 and "笔记已保存入库" in calls[1]
    row = records(config, entry)[0]
    value = json.loads(row["value"])
    assert value["links"][0]["publication"]["state"] == "published"
    assert len(value["receipt_updates"]) == 2
    assert {e["state"] for e in value["receipt_updates"].values()} == {"sent"}
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_failure_is_separate_from_received_and_not_repeated(
    config, entry, policy, monkeypatch, notify
):
    calls, _, deliver = notify
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    deliver()
    intake_extraction.update(
        config,
        entry[0]["id"],
        batch,
        0,
        {"state": "needs_review", "reason": "intake_x_article_serialization_invalid"},
    )
    automatic_ingestion.tick(config)
    deliver(4)
    assert len(calls) == 2 and "失败" in calls[1] and "尚未全部入库" in calls[1]
    assert "X文章" in calls[1] and "远端正文或视频可能尚未取得" in calls[1]
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == 0


def test_unknown_outcome_is_not_retried_after_new_adapter(
    config, entry, policy, monkeypatch, notify
):
    calls, adapter, deliver = notify
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    deliver()
    intake_extraction.update(
        config,
        entry[0]["id"],
        batch,
        0,
        {"state": "needs_review", "reason": "video_skill_init_job_failed"},
    )

    def unknown(owner, body, guard):
        guard()
        calls.append(body)
        return {"category": "transport_exception"}

    adapter._fiber_send_receipt = unknown
    deliver(3)
    fresh = SimpleNamespace(
        _fiber_send_receipt=lambda *a: pytest.fail("ambiguous send must not replay")
    )
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], fresh))
    assert len(calls) == 2
    value = json.loads(records(config, entry)[0]["value"])
    assert value["receipt_state"] == "unknown"
    assert value["receipt_updates"][value["receipt_event"]]["state"] == "unknown"


def test_legacy_sent_receipt_does_not_backfill_messages(config, entry, policy, monkeypatch, notify):
    calls, _, deliver = notify
    capture(config, entry, policy, monkeypatch, "basic", True)
    with channels.opened(config) as db:
        row = db.execute("SELECT * FROM channel_intake_records").fetchone()
        value = json.loads(row["value"])
        value.pop("receipt_tracking")
        value["receipt_state"] = "sent"
        raw = json.dumps(value)
        db.execute("UPDATE channel_intake_records SET value=?", (raw,))
    for _ in range(3):
        automatic_ingestion.tick(config)
    deliver(3)
    assert not calls
    assert "receipt_updates" not in json.loads(records(config, entry)[0]["value"])


def test_manual_review_is_not_reported_as_saved(config, entry, policy, monkeypatch, notify):
    calls, _, deliver = notify
    capture(config, entry, policy, monkeypatch, "basic", False)
    deliver()
    automatic_ingestion.tick(config)
    deliver(3)
    assert len(calls) == 2 and "等待人工核验" in calls[1]
    assert "笔记已保存入库" not in calls[1]


def test_saved_visual_note_receipt_discloses_unverified_speech(
    config, entry, policy, monkeypatch, notify
):
    calls, _, deliver = notify
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    intake_extraction.update(
        config, entry[0]["id"], batch, 0, {"metadata": {"transcript_route": "visual_primary"}}
    )
    deliver()
    for _ in range(3):
        automatic_ingestion.tick(config)
    deliver(3)
    assert len(calls) == 2 and "笔记已保存入库" in calls[1]
    assert "语音内容未核验" in calls[1]


def test_index_failure_preserves_saved_note_status(config, entry, policy, monkeypatch, notify):
    calls, _, deliver = notify
    capture(config, entry, policy, monkeypatch, "basic", True)
    deliver()
    for _ in range(2):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        identity = db.execute("SELECT id FROM automatic_jobs WHERE kind='intake'").fetchone()[0]
        job = automatic_jobs.get(db, identity)
        assert job["phase"] == "index" and job["value"].get("publication")
        automatic_jobs.settle(db, job, "needs_review", reason="note_index_unavailable")
    deliver(3)
    assert len(calls) == 2 and "笔记已保存入库，检索索引更新失败" in calls[1]
    assert "提取或处理失败" not in calls[1]


def test_explicit_retry_keeps_old_receipts_and_reports_new_success(
    config, entry, policy, monkeypatch, notify
):
    from test_article_fetch import Reply, install
    from test_articles import PNG, current_html

    calls, _, deliver = notify
    batch = capture(config, entry, policy, monkeypatch, "basic", True)
    deliver()
    intake_extraction.update(
        config,
        entry[0]["id"],
        batch,
        0,
        {"state": "needs_review", "reason": "intake_x_article_serialization_invalid"},
    )
    automatic_ingestion.tick(config)
    deliver()
    before = json.loads(records(config, entry)[0]["value"])["receipt_updates"]
    row = records(config, entry)[0]
    intake_extraction.retry(config, entry[0]["id"], batch, 0, row["revision"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    intake_extraction.advance(config, entry[0]["id"])
    for _ in range(3):
        automatic_ingestion.tick(config)
    deliver(3)
    assert len(calls) == 3 and "笔记已保存入库" in calls[-1]
    after = json.loads(records(config, entry)[0]["value"])["receipt_updates"]
    assert all(after[k] == v for k, v in before.items())


def test_pending_old_rows_do_not_starve_later_receipts(config, entry, policy, monkeypatch, notify):
    import uuid

    calls, _, deliver = notify
    capture(config, entry, policy, monkeypatch, "basic", True)
    with channels.opened(config) as db:
        row = dict(db.execute("SELECT * FROM channel_intake_records").fetchone())
        base = json.loads(row["value"])
        for i in range(21):
            value = {
                **base,
                "links": [{"state": "pending", "original_url": "https://x.com/synthetic/status/1"}],
                "receipt_state": "sent",
                "receipt_tracking": {"version": 2, "next_check": 0},
                "automatic_imports": [],
            }
            db.execute(
                "INSERT INTO channel_intake_records VALUES(?,?,?,?,?,?,?,?)",
                (
                    row["channel_id"],
                    str(uuid.uuid4()),
                    row["revision"],
                    "stored",
                    None,
                    json.dumps(value),
                    row["created"] - 22 + i,
                    row["updated"],
                ),
            )
    deliver(3)
    assert len(calls) == 1 and "消息已接收" in calls[0]


@pytest.mark.parametrize("held_state", ["unknown", "sending"])
def test_ambiguous_old_batch_does_not_block_new_batch_receipts(
    config, entry, policy, monkeypatch, notify, held_state
):
    from test_channel_intake import receive

    from fiber_bundle.channels import intake_processing
    from fiber_bundle.channels.intake_receipt_health import summary

    calls, _, deliver = notify
    old = capture(config, entry, policy, monkeypatch, "basic", True)
    with channels.opened(config) as db:
        row = db.execute("SELECT * FROM channel_intake_records WHERE batch_id=?", (old,)).fetchone()
        value = json.loads(row["value"])
        value.update(receipt_state=held_state, receipt_event="received")
        value["receipt_updates"] = {"received": {"state": held_state, "body": "old receipt"}}
        wire = json.dumps(value)
        db.execute("UPDATE channel_intake_records SET value=? WHERE batch_id=?", (wire, old))
    new = receive(config, entry, identity="new-receipt", text="新批次资料")
    intake_processing.advance(config, entry[0]["id"])
    status = summary(config, entry[0]["id"], row["revision"], True)
    assert status["needs_review_batches"] + status["sending_batches"] == 1
    assert status["pending_batches"] == 1
    deliver(4)
    assert len(calls) == 2
    assert all(new in body and old not in body for body in calls)
    assert "消息已接收" in calls[0] and "仅消息/附件已归档" in calls[1]
    assert "尚未生成笔记" in calls[1]
    with channels.readonly(config) as db:
        assert (
            db.execute(
                "SELECT value FROM channel_intake_records WHERE batch_id=?", (old,)
            ).fetchone()[0]
            == wire
        )
        saved = json.loads(
            db.execute(
                "SELECT value FROM channel_intake_records WHERE batch_id=?", (new,)
            ).fetchone()[0]
        )
    assert saved["receipt_state"] == "sent"
    assert len(saved["receipt_updates"]) == 2
    # A new adapter/process cannot replay either the ambiguous old send or sent new sends.
    fresh = SimpleNamespace(_fiber_send_receipt=lambda *args: pytest.fail("no send may repeat"))
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], fresh))
