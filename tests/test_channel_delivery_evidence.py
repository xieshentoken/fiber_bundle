"""Persist finite send evidence once, retaining parts and never retrying unknown sends."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import database, feishu_delivery
from fiber_bundle.channels import dispatch, store
from fiber_bundle.config import IntegrityError, InvalidInput


def ready(config, channel):
    entry = bind(config, channel[0])
    assert dispatch.capture(
        config, entry["id"], 1, "delivery-evidence", entry["owner"], "查询：压力"
    )
    dispatch.advance(config, entry["id"], 1)
    return entry


def record(config, entry):
    with store.readonly(config) as db:
        row = db.execute(
            "SELECT state,value FROM events WHERE event_id=?", ("delivery-evidence",)
        ).fetchone()
    return row["state"], json.loads(row["value"])


@pytest.mark.parametrize(
    "error,code",
    [
        (database.LockTimeout("private-token-do-not-log"), "channel_storage_busy"),
        (
            IntegrityError("cloud_item_pending_stale_or_conflicted"),
            "cloud_item_pending_stale_or_conflicted",
        ),
        (RuntimeError("private-token-do-not-log"), "channel_internal_failed"),
    ],
)
def test_send_failure_is_durable_private_and_never_replayed(config, channel, error, code):
    entry = ready(config, channel)
    calls = []

    async def sender(owner, body, context, guard):
        guard()
        calls.append(True)
        raise error

    assert asyncio.run(dispatch.deliver(config, entry["id"], 1, sender)) == "unknown"
    state, value = record(config, entry)
    assert state == "unknown"
    assert value["delivery_outcome"]["failure"]["error"] == code
    assert "private-token" not in json.dumps(value)
    with pytest.raises(InvalidInput, match="requires_reconciliation"):
        asyncio.run(dispatch.deliver(config, entry["id"], 1, sender))
    assert calls == [True]
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_configuration_calls").fetchone()[0] == 0


def test_provider_rejection_retains_numeric_code_only(config, channel):
    entry = ready(config, channel)

    async def sender(owner, body, context, guard):
        guard()
        guard.delivery_evidence = feishu_delivery.response_evidence(
            SimpleNamespace(success=lambda: False, code=99991663, msg="private-token-do-not-log")
        )
        return False

    assert asyncio.run(dispatch.deliver(config, entry["id"], 1, sender)) == "unknown"
    _, value = record(config, entry)
    assert value["delivery_outcome"]["evidence"] == {
        "category": "negative_ack",
        "provider_code": "99991663",
    }
    assert "private-token" not in json.dumps(value)


def test_success_settlement_preserves_weixin_part_receipts(config, channel):
    entry = ready(config, channel)
    with store.opened(config) as db:
        value = entry["value"] | {"platform": "weixin"}
        db.execute("UPDATE channels SET value=? WHERE id=?", (json.dumps(value), entry["id"]))

    async def sender(owner, body, context, guard):
        guard()
        guard.part("1", "text", "sending")
        guard.part("1", "text", "sent")
        return True

    assert asyncio.run(dispatch.deliver(config, entry["id"], 1, sender)) == "sent"
    _, value = record(config, entry)
    assert value["delivery_parts"]["1"]["state"] == "sent"
    assert value["delivery_outcome"]["state"] == "sent"
