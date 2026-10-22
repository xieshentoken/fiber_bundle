"""One guarded Feishu result send; protocol evidence never implies user receipt."""

import importlib
import json
import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from . import database, feishu_budgets, feishu_sync, weixin_inbox
from . import weixin_task_delivery as delivery
from . import weixin_task_results as results
from .config import Config, InvalidInput
from .weixin_delivery_state import ensure_resolved
from .weixin_events import Binding


def response_evidence(response: Any) -> dict[str, str]:
    """Only finite provider codes and a hashed receipt; never SDK messages or headers."""
    if response is None:
        return {"category": "missing_response"}
    if not response.success():
        code = getattr(response, "code", None)
        return {
            "category": "negative_ack",
            **({"provider_code": str(code)} if type(code) is int and abs(code) <= 2**31 else {}),
        }
    identity = getattr(getattr(response, "data", None), "message_id", None)
    if not isinstance(identity, str) or not 0 < len(identity) <= 400:
        return {"category": "missing_message_id"}
    from .objects import digest

    return {"category": "confirmed", "message_id_sha256": digest(identity.encode())}


def sdk_send(
    sdk: Any, client: Any, target: str, body: str, guard: Callable[[], None]
) -> dict[str, str]:
    """Pinned SDK, finite client timeout, one POST, no ordinary Agent send capability."""
    models = importlib.import_module("lark_oapi.api.im.v1")
    request = (
        models.CreateMessageRequest.builder()
        .receive_id_type("open_id")
        .request_body(
            models.CreateMessageRequestBody.builder()
            .receive_id(target)
            .msg_type("text")
            .content(json.dumps({"text": body}, ensure_ascii=False))
            .uuid(str(uuid4()))
            .build()
        )
        .build()
    )
    guard()
    try:
        response = client.im.v1.message.create(request)
        return response_evidence(response)
    except Exception:
        return {"category": "transport_exception"}


def claim(config: Config, binding: Binding, batch: str, grant: str | None) -> dict[str, str]:
    prepared = delivery.prepare(config, binding, batch)
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        ensure_resolved(db, binding)
        body, fingerprint = results.snapshot_locked(config, db, binding, batch)
        row = db.execute("SELECT * FROM wx_task_deliveries WHERE batch_id=?", (batch,)).fetchone()
        if row["state"] != "pending":
            return {"state": row["state"]}
        if body != prepared["text"] or fingerprint != row["result_sha256"]:
            raise InvalidInput("Feishu delivery changed")
        if not feishu_budgets.reserve_locked(db, binding, grant, "outbound", batch):
            return {"state": "budget_exhausted"}
        db.execute("UPDATE wx_task_deliveries SET state='sending' WHERE batch_id=?", (batch,))
        db.execute(
            "CREATE TABLE IF NOT EXISTS fiber_feishu_delivery_evidence("
            "batch_id TEXT PRIMARY KEY REFERENCES wx_task_deliveries(batch_id),"
            "category TEXT NOT NULL,message_id_sha256 TEXT) STRICT"
        )
        db.execute("INSERT INTO fiber_feishu_delivery_evidence VALUES(?,'claimed',NULL)", (batch,))
        return {"state": "sending", "text": body}


def settle(config: Config, binding: Binding, batch: str, outcome: dict[str, str]) -> dict[str, str]:
    allowed = {
        "confirmed",
        "missing_response",
        "negative_ack",
        "missing_message_id",
        "transport_exception",
        "guard_rejected",
        "invalid_transport_result",
    }
    category = outcome.get("category", "invalid_transport_result")
    if category not in allowed:
        category = "invalid_transport_result"
    fingerprint = outcome.get("message_id_sha256")
    if fingerprint is not None and not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        fingerprint = None
    state = "sent" if category == "confirmed" and fingerprint else "unknown"
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        weixin_inbox.bound(db, binding)
        db.execute(
            "UPDATE wx_task_deliveries SET state=? WHERE batch_id=? AND state='sending'",
            (state, batch),
        )
        db.execute(
            "UPDATE fiber_feishu_delivery_evidence SET category=?,message_id_sha256=? "
            "WHERE batch_id=?",
            (category, fingerprint, batch),
        )
    return {"state": state, "category": category}


async def deliver(
    config: Config,
    binding: Binding,
    batch: str,
    grant: str | None,
    sender: Callable[[str, str, Callable[[], None]], dict[str, str]],
) -> dict[str, str]:
    claimed = await feishu_sync.run(claim, config, binding, batch, grant)
    if claimed["state"] != "sending":
        return claimed
    checked = False

    def guard() -> None:
        nonlocal checked
        if checked:
            raise InvalidInput("Feishu transport cannot replay")
        delivery._authorize(config, binding, batch, claimed["text"])
        checked = True

    try:
        outcome = await feishu_sync.run(sender, binding.owner_id, claimed["text"], guard)
        if not checked:
            outcome = {"category": "guard_rejected"}
    except Exception:
        outcome = {"category": "guard_rejected" if not checked else "transport_exception"}
    return await feishu_sync.run(settle, config, binding, batch, outcome)
