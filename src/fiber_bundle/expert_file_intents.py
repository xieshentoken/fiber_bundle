"""Owner-private, whole-message expert intent binding; never guess a nearby source file."""

import json
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from . import auth, database, expert_command_schema, weixin_inbox
from .attachment_projection import epoch
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding

ALIAS = re.compile(r"用\s*fake-expert\s*处理文件", re.ASCII)
CANCEL = "取消fake-expert"
SELECT = re.compile(r"选择文件\s*([1-9][0-9]?)", re.ASCII)
TTL = 300


def direct(binding: Binding, message: dict[str, Any]) -> bool:
    raw = message.get("fiber_feishu", {}).get("raw_event", {}).get("event", {})
    sender = raw.get("sender", {})
    body = raw.get("message", {})
    # A normalized file is archival data; its generic forwarded flag is not a control flag.
    return (
        sender.get("sender_type") == "user"
        and sender.get("sender_id", {}).get("open_id") == binding.owner_id
        and body.get("chat_type") == "p2p"
        and not body.get("merge_forward_id")
        and not body.get("forwarded")
        and not raw.get("forwarded")
    )


def command_text(binding: Binding, message: dict[str, Any]) -> str | None:
    if not direct(binding, message) or message.get("forwarded"):
        return None
    raw = message["fiber_feishu"]["raw_event"]["event"]["message"]
    if raw.get("message_type") != "text":
        return None
    text = json.loads(raw["content"]).get("text")
    if not isinstance(text, str):
        return None
    value = text.strip()
    return value if ALIAS.fullmatch(value) or value == CANCEL or SELECT.fullmatch(value) else None


def scope(config: Config, binding: Binding, chat: str) -> dict[str, Any]:
    with database.connect(config.state_root) as main:
        auth.grant(main, binding.client_id, binding.vault_id, "add")
        auth.grant(main, binding.client_id, binding.vault_id, "original")
        permission = epoch(main, binding.client_id, binding.vault_id)
    return {**binding.__dict__, "chat_id": chat, "permission_epoch": permission}


def expire(db: sqlite3.Connection, now: float) -> None:
    db.execute(
        (
            "UPDATE fiber_expert_commands SET state='expired' WHERE state IN ("
            "'waiting_file','needs_selection','awaiting_download') AND json_ex"
            "tract(request_json,'$.intent.expires_at')<?"
        ),
        (now,),
    )


def capture(
    config: Config,
    binding: Binding,
    message: dict[str, Any],
    authorization: str,
    maximum: int,
    *,
    guard: Callable[[], None] | None = None,
    unlimited: bool = False,
) -> bool:
    text = command_text(binding, message)
    if text is None:
        return False
    raw = message["fiber_feishu"]["raw_event"]["event"]["message"]
    chat = raw["chat_id"]
    bound = scope(config, binding, chat)
    now = time.time()
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        if guard is not None:
            guard()
        expert_command_schema.initialize(db)
        weixin_inbox.bound(db, binding)
        expire(db, now)
        old = db.execute(
            "SELECT * FROM fiber_expert_commands WHERE authorization=? AND event_id=?",
            (authorization, message["message_id"]),
        ).fetchone()
        fingerprint = digest(canonical(message).encode())
        if old is not None:
            if json.loads(old["request_json"])["message_sha256"] != fingerprint:
                raise InvalidInput("expert_feishu_command_changed")
            return True
        active = db.execute(
            (
                "SELECT * FROM fiber_expert_commands WHERE authorization=? AND sta"
                "te IN ('waiting_file','needs_selection','awaiting_download') AND "
                "json_extract(request_json,'$.intent.scope.chat_id')=? ORDER BY ro"
                "wid DESC LIMIT 1"
            ),
            (authorization, chat),
        ).fetchone()
        if text == CANCEL or SELECT.fullmatch(text):
            control = db.execute(
                "SELECT sha256 FROM fiber_expert_control_events "
                "WHERE authorization=? AND event_id=?",
                (authorization, message["message_id"]),
            ).fetchone()
            if control:
                if control[0] != fingerprint:
                    raise InvalidInput("expert_feishu_control_changed")
                return True
            db.execute(
                "INSERT INTO fiber_expert_control_events VALUES(?,?,?)",
                (authorization, message["message_id"], fingerprint),
            )
        if text == CANCEL:
            if active:
                db.execute(
                    (
                        "UPDATE fiber_expert_commands SET state='cancelled' WHERE authoriz"
                        "ation=? AND event_id=?"
                    ),
                    (authorization, active["event_id"]),
                )
            return True
        selected = SELECT.fullmatch(text)
        if selected:
            if active is None or active["state"] != "needs_selection":
                return True
            value = json.loads(active["request_json"])
            candidates = value["intent"].get("candidates", [])
            number = int(selected[1]) - 1
            if number >= len(candidates) or value["intent"]["scope"] != bound:
                raise InvalidInput("expert_feishu_selection_changed")
            value["intent"]["selected_file"] = candidates[number]
            db.execute(
                (
                    "UPDATE fiber_expert_commands SET state='awaiting_download',reques"
                    "t_json=? WHERE authorization=? AND event_id=? AND state='needs_se"
                    "lection'"
                ),
                (canonical(value), authorization, active["event_id"]),
            )
            return True
        if active:
            return True  # A repeated instruction does not extend the one-shot expiry or quota.
        count = db.execute(
            "SELECT COUNT(*) FROM fiber_expert_commands WHERE authorization=?", (authorization,)
        ).fetchone()[0]
        if not unlimited and count >= maximum:
            raise InvalidInput("expert_feishu_budget_exhausted")
        reply = raw.get("parent_id")
        if reply is not None and (not isinstance(reply, str) or not reply or len(reply) > 400):
            raise InvalidInput("expert_feishu_reply_invalid")
        intent = {
            "scope": bound,
            "created_at": now,
            "expires_at": now + TTL,
            "reply_message_id": reply,
            "mode": "reply" if reply else "next_single_pdf",
            "consume_rule": "same_owner_account_chat_single_direct_pdf_once",
            "candidates": [],
        }
        value = {
            "binding": binding.__dict__,
            "maximum": maximum,
            "authorization": authorization,
            "message_sha256": fingerprint,
            "intent": intent,
            "selected": {},
        }
        db.execute(
            "INSERT INTO fiber_expert_commands VALUES(?,?,?, ?,NULL)",
            (
                authorization,
                message["message_id"],
                canonical(value),
                "awaiting_download" if reply else "waiting_file",
            ),
        )
    return True


def select_file(
    config: Config,
    binding: Binding,
    message: dict[str, Any],
    authorization: str,
    *,
    guard: Callable[[], None] | None = None,
) -> bool:
    """Bind only this incoming direct file; selection happens before ordinary auto admission."""
    if not direct(binding, message):
        return False
    raw = message["fiber_feishu"]["raw_event"]["event"]["message"]
    files = message["fiber_feishu"]["files"]
    if not files:
        return False
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        if guard is not None:
            guard()
        expert_command_schema.initialize(db)
        expire(db, time.time())
        row = db.execute(
            (
                "SELECT * FROM fiber_expert_commands WHERE authorization=? AND sta"
                "te='waiting_file' AND json_extract(request_json,'$.intent.scope.c"
                "hat_id')=? ORDER BY rowid LIMIT 1"
            ),
            (authorization, raw["chat_id"]),
        ).fetchone()
        if row is None:
            return False
        bound = scope(config, binding, raw["chat_id"])
        value = json.loads(row["request_json"])
        if value["intent"]["scope"] != bound:
            raise InvalidInput("expert_feishu_scope_changed")
        candidates = [
            {"event_id": message["message_id"], "item_key": key, **file}
            for key, file in files.items()
        ]
        value["intent"]["candidates"] = candidates
        if len(candidates) != 1:
            state = "needs_selection"
        elif raw.get("message_type") != "file" or not candidates[0]["name"].lower().endswith(
            ".pdf"
        ):
            # Wrong formats do not consume the one-shot intent or extend its deadline.
            value["intent"]["reason"] = "expert_feishu_pdf_required"
            state = "waiting_file"
        else:
            value["intent"]["selected_file"] = candidates[0]
            state = "awaiting_download"
        db.execute(
            (
                "UPDATE fiber_expert_commands SET state=?,request_json=? WHERE aut"
                "horization=? AND event_id=? AND state='waiting_file'"
            ),
            (state, canonical(value), authorization, row["event_id"]),
        )
        return state != "waiting_file"
