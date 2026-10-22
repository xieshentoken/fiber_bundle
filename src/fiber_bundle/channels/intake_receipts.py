"""Optional bounded Feishu acknowledgement; unknown sends are never retried."""

import json
import time
from typing import Any

from .. import database, feishu_sync
from ..config import Config, InvalidInput
from . import intake_repository as repository
from . import store
from .intake_processing import guard_locked, record
from .services import policy as service_policy


def take(
    config: Config, identity: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    channel, policy = repository.channel(config, identity)
    if not policy["settings"]["receipt"]:
        return None
    with database.locked(config), store.opened(config) as side:
        # An ambiguous send freezes only its own batch, never unrelated receipts.
        # The storage lease and committed "sending" claim still prevent duplicates.
        rows = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND revision=? "
            "AND created>=? AND coalesce(json_extract(value,'$.receipt_state'),'') "
            "NOT IN ('sending','unknown') AND (state='stored' OR (state='needs_review' "
            "AND json_type(value,'$.failure')='object')) "
            "AND (json_extract(value,'$.receipt_state') IS NULL OR "
            "(json_extract(value,'$.receipt_tracking.version')=2 AND "
            "coalesce(json_extract(value,'$.receipt_tracking.next_check'),0)<=? AND "
            "coalesce(json_extract(value,'$.receipt_tracking.done'),0)=0)) "
            "ORDER BY coalesce(json_extract(value,'$.receipt_tracking.next_check'),0),created "
            "LIMIT 20",
            (identity, policy["revision"], service_policy.cutoff(config, identity), time.time()),
        ).fetchall()
        from .intake_outcomes import outcome

        for row in rows:
            value = json.loads(row["value"])
            tracked = value.get("receipt_tracking", {}).get("version") == 2
            updates = value.setdefault("receipt_updates", {})
            event: dict[str, Any] | None
            if value.get("failure"):
                with database.connect(config.state_root) as main:
                    event = outcome(main, channel, row["batch_id"], value)
            elif value.get("receipt_state") is None:
                event = {
                    "key": "received",
                    "stage": "received",
                    "body": (
                        f"消息已接收，批次 {row['batch_id']}。\n"
                        "收到的消息/附件及链接已归档；远端内容提取和笔记入库结果将另行通知。"
                    ),
                }
            elif tracked:
                with database.connect(config.state_root) as main:
                    event = outcome(main, channel, row["batch_id"], value)
            else:
                continue
            guard_locked(config, identity, policy["revision"], manual=bool(value.get("manual")))
            with database.transaction(side):
                if tracked:
                    value["receipt_tracking"]["next_check"] = time.time() + 5
                    if event and event.get("done"):
                        value["receipt_tracking"]["done"] = True
                if event and event["key"] not in updates:
                    event = {**event, "state": "sending", "observed_at": time.time()}
                    if not repository.reserve(
                        side,
                        identity,
                        policy["settings"],
                        "outbound",
                        row["batch_id"] + ":" + event["key"],
                    ):
                        event["state"] = "budget_exhausted"
                    updates[event["key"]] = event
                    value.update(receipt_state=event["state"], receipt_event=event["key"])
                    chosen = event["state"] == "sending"
                else:
                    chosen = False
                record(
                    side,
                    identity,
                    row["batch_id"],
                    row["revision"],
                    row["state"],
                    row["reason"],
                    value,
                )
            if chosen:
                return channel, dict(row), value
    return None


async def deliver(config: Config, identity: str, adapter: Any) -> None:
    chosen = await feishu_sync.run(take, config, identity)
    if chosen is None:
        return
    channel, row, value = chosen
    event_key = value["receipt_event"]
    body = value["receipt_updates"][event_key]["body"]

    def guard() -> None:
        with database.locked(config):
            guard_locked(config, identity, row["revision"], manual=bool(value.get("manual")))

    state = "unknown"
    result = {}
    try:
        if channel.get("origin") == "legacy":
            result = await feishu_sync.run(
                adapter._fiber_send_receipt, channel["owner"], body, guard
            )
            state = "sent" if result.get("category") == "confirmed" else "unknown"
        else:
            from .feishu import send

            state = "sent" if await send(adapter, channel["owner"], body, "", guard) else "unknown"
    finally:
        value["receipt_state"] = state
        with database.locked(config), store.opened(config) as side, database.transaction(side):
            latest = side.execute(
                "SELECT value FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                (identity, row["batch_id"]),
            ).fetchone()
            merged = {**json.loads(latest[0]), "receipt_state": state}
            merged["receipt_updates"][event_key]["state"] = state
            if result.get("message_id_sha256"):
                merged["receipt_updates"][event_key]["message_id_sha256"] = result[
                    "message_id_sha256"
                ]
            record(
                side,
                identity,
                row["batch_id"],
                row["revision"],
                row["state"],
                row["reason"],
                merged,
            )


def reconcile(
    config: Config, identity: str, batch: str, acknowledgement: str, *, leased: bool = False
) -> dict[str, str]:
    if acknowledgement not in {"sent", "not_sent", "unknown"}:
        raise InvalidInput("intake_receipt_ack_invalid")
    channel, _ = repository.channel(config, identity)
    if channel["state"] == "running":
        raise InvalidInput("intake_stop_before_recovery")
    from .. import feishu_runtime
    from . import control, legacy

    runtime = (
        feishu_runtime.inspect(legacy.home(config, channel))
        if channel.get("origin") == "legacy"
        else control.status(config, identity)
    )
    if runtime["process_state"] != "stopped":
        raise InvalidInput("intake_stop_before_recovery")
    with repository.lease(config, leased), store.opened(config) as db, database.transaction(db):
        row = db.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (identity, batch),
        ).fetchone()
        if row is None or json.loads(row["value"]).get("receipt_state") not in {
            "unknown",
            "sending",
        }:
            raise InvalidInput("intake_receipt_not_unresolved")
        value = {**json.loads(row["value"]), "receipt_state": acknowledgement}
        if value.get("receipt_event"):
            value["receipt_updates"][value["receipt_event"]]["state"] = acknowledgement
        record(db, identity, batch, row["revision"], row["state"], row["reason"], value)
    return {"receipt_state": acknowledgement, "replay": "disabled"}
