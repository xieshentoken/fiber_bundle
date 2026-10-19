"""Private expert intent status and explicit UNKNOWN acknowledgement accounting."""

import json
from typing import Any, Literal

from fastapi import APIRouter, Request

from .. import auth, database, weixin_inbox
from ..config import Config, InvalidInput
from ..inputs import canonical
from ..payloads import Payload
from ..query_http import access
from . import configuration, control, expert, store


class Reconcile(Payload):
    for_state: str
    acknowledgement: Literal["sent", "not_sent", "unknown"]


def channel(config: Config, identity: str) -> dict[str, Any]:
    with store.readonly(config) as side:
        if side is None:
            raise auth.Denied(404)
        value = store.get(side, identity)
    if value["value"]["platform"] != "feishu":
        raise InvalidInput("channel_expert_requires_feishu")
    return value


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.get("/{identity}/expert-intents")
    def listing(identity: str, request: Request) -> list[dict[str, Any]]:
        with access(config, request, admin=True):
            value = channel(config, identity)
            if not (config.state_root / "weixin-inbox.sqlite3").exists():
                return []
            binding = configuration.feishu_settings(config, value).binding
            with weixin_inbox.connection(config, binding, leased=True) as inbox:
                if (
                    inbox.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='fiber_expert_commands'"
                    ).fetchone()
                    is None
                ):
                    return []
                return [
                    {
                        "event_id": row["event_id"],
                        "state": row["state"],
                        "current_authorization": row["authorization"]
                        == expert.authorization(identity, value["revision"]),
                        "task_id": row["task_id"],
                        "expires_at": payload["intent"]["expires_at"],
                        "reason": payload["intent"].get("reason"),
                        "ack": payload["intent"].get("ack"),
                        "ordinary_requests": payload["intent"].get("ordinary_requests", []),
                        "candidates": [
                            {"name": c["name"]} for c in payload["intent"].get("candidates", [])
                        ],
                    }
                    for row in inbox.execute(
                        "SELECT * FROM fiber_expert_commands WHERE json_extract(request_js"
                        "on,'$.intent.scope.account_id')=? ORDER BY rowid DESC LIMIT 100",
                        (binding.account_id,),
                    )
                    for payload in [json.loads(row["request_json"])]
                    if "intent" in payload
                ]

    @routes.post("/{identity}/expert-intents/{event_id}/reconcile")
    def reconcile(
        identity: str, event_id: str, data: Reconcile, request: Request
    ) -> dict[str, Any]:
        with access(config, request, admin=True):
            value = channel(config, identity)
            if (
                value["state"] not in {"paused", "error", "unpaired"}
                or control.status(config, identity)["process_state"] != "stopped"
            ):
                raise InvalidInput("channel_pause_required")
            binding = configuration.feishu_settings(config, value).binding
            with (
                weixin_inbox.connection(config, binding, leased=True) as inbox,
                database.transaction(inbox),
            ):
                rows = inbox.execute(
                    "SELECT * FROM fiber_expert_commands WHERE json_extract(request_js"
                    "on,'$.intent.scope.account_id')=? AND event_id=?",
                    (binding.account_id, event_id),
                ).fetchall()
                if len(rows) != 1:
                    raise InvalidInput("expert_receipt_identity_ambiguous_or_missing")
                row = rows[0]
                payload = json.loads(row["request_json"])
                ack = payload.get("intent", {}).get("ack", {})
                if (
                    ack.get("state") not in {"sending", "unknown"}
                    or ack.get("for_state") != data.for_state
                ):
                    raise InvalidInput("expert_receipt_reconciliation_changed")
                ack["state"] = data.acknowledgement
                ack["reconciled"] = True
                inbox.execute(
                    "UPDATE fiber_expert_commands SET request_json=? WHERE authorizati"
                    "on=? AND event_id=?",
                    (canonical(payload), row["authorization"], event_id),
                )
                return {
                    "acknowledgement": data.acknowledgement,
                    "replay": False,
                    "budget_reset": False,
                }

    return routes
