"""Frozen-scope confirmation prompts and exact owner commands, without model dispatch."""

import json
import re
import secrets
import sqlite3
from typing import Any

from . import database
from . import weixin_batches as batches
from . import weixin_inbox as inbox
from . import weixin_intents as intents
from .config import Config, IntegrityError, InvalidInput
from .objects import digest
from .weixin_events import Binding

ACTIONS = {
    "保存为一份": "save_one",
    "分别保存": "save_separately",
    "仅用于咨询": "consult",
    "取消": "cancel",
}
COMMAND = re.compile(
    r"批次 ([0-9a-f]{16}) (保存为一份|分别保存|仅用于咨询|取消)(?:\n问题：([^\n]{1,4000}))?"
)


def initialize(config: Config, binding: Binding) -> None:
    batches.initialize(config, binding)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_confirmation_prompts("
            "id TEXT PRIMARY KEY,account_id TEXT NOT NULL,"
            "batch_id TEXT NOT NULL REFERENCES wx_batches(id),"
            "members_json TEXT NOT NULL,body TEXT NOT NULL,"
            "state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','unknown')),"
            "UNIQUE(account_id,batch_id)) STRICT"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS wx_command_receipts("
            "account_id TEXT NOT NULL,event_id TEXT NOT NULL,raw_sha256 TEXT NOT NULL,"
            "kind TEXT NOT NULL CHECK(kind IN ('source','command')),result_json TEXT NOT NULL,"
            "PRIMARY KEY(account_id,event_id),"
            "FOREIGN KEY(account_id,event_id) REFERENCES events(account_id,event_id)) STRICT"
        )
        # Existing batch membership must never be reclassified as an executable command.
        db.execute(
            "INSERT OR IGNORE INTO wx_command_receipts "
            "SELECT e.account_id,e.event_id,e.raw_sha256,'source','{}' FROM events e "
            "JOIN wx_batch_members m ON m.account_id=e.account_id AND m.event_id=e.event_id "
            "WHERE e.account_id=?",
            (binding.account_id,),
        )


def members(db: sqlite3.Connection, binding: Binding, batch_id: str) -> str:
    rows = db.execute(
        "SELECT e.job_id,e.raw_sha256,e.raw FROM wx_batch_members m JOIN events e "
        "ON e.account_id=m.account_id AND e.event_id=m.event_id "
        "WHERE m.batch_id=? AND m.account_id=? ORDER BY m.ordinal",
        (batch_id, binding.account_id),
    ).fetchall()
    if any(digest(bytes(r["raw"])) != r["raw_sha256"] for r in rows):
        raise IntegrityError("confirmation member digest mismatch")
    return json.dumps([[r["job_id"], r["raw_sha256"]] for r in rows])


def prepare(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        view = batches.snapshot(db, binding, batch_id)
        saved = db.execute(
            "SELECT * FROM wx_confirmation_prompts WHERE account_id=? AND batch_id=?",
            (binding.account_id, batch_id),
        ).fetchone()
        scope = members(db, binding, batch_id)
        if saved is not None:
            if saved["members_json"] != scope:
                raise IntegrityError("confirmation scope changed")
            return {"prompt_id": saved["id"], "text": saved["body"], "state": saved["state"]}
        if view["state"] != "frozen" or not view["members"]:
            raise InvalidInput("confirmation requires a frozen nonempty batch")
        token = secrets.token_hex(8)
        body = f"批次 {token}：已固定 {len(view['members'])} 条资料。\n"
        intent = intents.read_locked(db, binding, batch_id)
        if intent is not None:
            body = (
                f"批次 {token}：已固定 {len(view['members'])} 条消息，"
                f"含 {intent['source_count']} 条原资料。\n"
            )
            if intent["reason"]:
                body += "需澄清资料范围或处理意图；问题和操作语句不会作为原资料入库。\n"
                if not intent["source_count"]:
                    candidates = db.execute(
                        "SELECT p.id FROM wx_confirmation_prompts p JOIN wx_batches b "
                        "ON b.id=p.batch_id WHERE p.account_id=? AND b.id!=? "
                        "AND b.state IN ('frozen','selected') ORDER BY p.rowid DESC LIMIT 3",
                        (binding.account_id, batch_id),
                    ).fetchall()
                    body += "请明确资料或回复目标批次的咨询命令，并补充问题。\n"
                    if candidates:
                        body += "候选批次：" + "、".join(r["id"] for r in candidates) + "。\n"
            elif intent["question"]:
                body += "已单列咨询问题；本批次尚未保存。\n"
                body += (
                    "你同时要求保存原资料；选择保存操作会保留原资料并咨询，"
                    "选择仅用于咨询则不入库。\n"
                    if intent["also_save"]
                    else "带问题默认仅咨询；需要归档原资料请明确选择保存。\n"
                )
        body += "请复制其中一条完整命令回复：\n"
        body += "\n".join(f"批次 {token} {label}" for label in ACTIONS)
        body += "\n咨询可另起一行填写“问题：你的问题”；没有问题会等待补充。"
        body += "\n缺件或未知类型须先处理，才能保存或咨询。选择后不代表已归档或回答。"
        db.execute(
            "INSERT INTO wx_confirmation_prompts VALUES(?,?,?,?,?,'pending')",
            (token, binding.account_id, batch_id, scope, body),
        )
        return {"prompt_id": token, "text": body, "state": "pending"}


def command_text(message: dict[str, Any]) -> str | None:
    items = message.get("item_list")
    if any(message.get(key) for key in ("ref_msg", "forward_info", "forwarded")):
        return None
    if not isinstance(items, list) or len(items) != 1:
        return None
    item = items[0]
    if (
        not isinstance(item, dict)
        or set(item) != {"type", "text_item"}
        or type(item["type"]) is not int
        or item["type"] != 1
    ):
        return None
    content = item["text_item"]
    if not isinstance(content, dict) or set(content) != {"text"}:
        return None
    value = content["text"]
    return (
        value if isinstance(value, str) and value.startswith(("批次 ", "术语 ", "文档 ")) else None
    )


def process(
    config: Config, binding: Binding, *, limit: int = 100, intent_routing: bool = False
) -> int:
    """Classify a bounded page before batching. Repeat deliveries cannot repeat choices."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("invalid command page")
    if type(intent_routing) is not bool:
        raise InvalidInput("intent routing switch must be boolean")
    if intent_routing:
        intents.initialize(config, binding)
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        events = db.execute(
            "SELECT e.* FROM events e WHERE e.account_id=? AND NOT EXISTS "
            "(SELECT 1 FROM wx_command_receipts c WHERE c.account_id=e.account_id "
            "AND c.event_id=e.event_id) ORDER BY e.rowid LIMIT ?",
            (binding.account_id, limit),
        ).fetchall()
        for event in events:
            if digest(bytes(event["raw"])) != event["raw_sha256"]:
                raise IntegrityError("command event digest mismatch")
            message = json.loads(event["raw"])
            if (
                message.get("from_user_id") != binding.owner_id
                or message.get("to_user_id") != binding.account_id
                or message.get("room_id")
                or message.get("chat_room_id")
            ):
                raise IntegrityError("command envelope mismatch")
            value = command_text(message)
            result: dict[str, Any] = {}
            kind = "source" if value is None else "command"
            if value is None and intent_routing:
                intents.capture_locked(db, event, message)
                result = {"intent_version": 2}
            if value is not None:
                if value.startswith("文档 "):
                    from .weixin_document_commands import apply_locked as document_command

                    result = document_command(config, binding, event["event_id"], value)
                elif value.startswith("术语 "):
                    from .weixin_media_terminology import apply_locked

                    result = apply_locked(config, db, binding, value)
                else:
                    match = COMMAND.fullmatch(value)
                    result = {"state": "rejected", "error_code": "invalid_batch_command"}
                    if match is not None:
                        token, label, question = match.groups()
                        prompt = db.execute(
                            "SELECT * FROM wx_confirmation_prompts WHERE id=? AND account_id=?",
                            (token, binding.account_id),
                        ).fetchone()
                        if prompt is None:
                            result["error_code"] = "unknown_confirmation"
                        elif prompt["members_json"] != members(db, binding, prompt["batch_id"]):
                            raise IntegrityError("confirmation scope changed")
                        else:
                            try:
                                choice = batches.choose_in_transaction(
                                    db,
                                    binding,
                                    prompt["batch_id"],
                                    ACTIONS[label],
                                    question=question,
                                )
                                reply = "批次选择已记录，尚未归档或回答。"
                                if choice["needs_question"]:
                                    reply = (
                                        f"请补充咨询问题，回复：批次 {token} 仅用于咨询"
                                        "\n问题：你的问题"
                                    )
                                elif choice["state"] == "cancelled":
                                    reply = "批次已取消，原始收件保留。"
                                result = {"state": "accepted", "batch": choice, "reply_text": reply}
                            except InvalidInput:
                                result["error_code"] = "batch_choice_rejected"
            db.execute(
                "INSERT INTO wx_command_receipts VALUES(?,?,?,?,?)",
                (
                    binding.account_id,
                    event["event_id"],
                    event["raw_sha256"],
                    kind,
                    json.dumps(result),
                ),
            )
        return len(events)


def receipt(config: Config, binding: Binding, job_id: str) -> dict[str, Any]:
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        row = db.execute(
            "SELECT c.result_json FROM wx_command_receipts c JOIN events e "
            "ON c.account_id=e.account_id AND c.event_id=e.event_id "
            "WHERE e.account_id=? AND e.job_id=?",
            (binding.account_id, job_id),
        ).fetchone()
        if row is None:
            raise InvalidInput("command not classified")
        return dict(json.loads(row[0]))


def list_prompts(config: Config, binding: Binding, *, after: int = 0) -> dict[str, Any]:
    if type(after) is not int or after < 0:
        raise InvalidInput("invalid confirmation page")
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        rows = db.execute(
            "SELECT rowid,* FROM wx_confirmation_prompts WHERE account_id=? AND rowid>? "
            "ORDER BY rowid LIMIT 101",
            (binding.account_id, after),
        ).fetchall()
        return {
            "prompts": [
                {
                    "prompt_id": row["id"],
                    "batch_id": row["batch_id"],
                    "text": row["body"],
                    "state": row["state"],
                }
                for row in rows[:100]
            ],
            "next_cursor": rows[99]["rowid"] if len(rows) > 100 else None,
        }


def tick(
    config: Config, binding: Binding, *, quiet_seconds: float = 3, intent_routing: bool = False
) -> list[dict[str, Any]]:
    process(config, binding, intent_routing=intent_routing)
    views = batches.advance(config, binding, quiet_seconds=quiet_seconds)
    # Recover a crash between freezing a batch and creating its prompt.
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        pending = db.execute(
            "SELECT b.id FROM wx_batches b WHERE b.account_id=? AND b.state='frozen' "
            "AND NOT EXISTS (SELECT 1 FROM wx_confirmation_prompts p WHERE p.batch_id=b.id) "
            "ORDER BY b.rowid LIMIT 100",
            (binding.account_id,),
        ).fetchall()
    for row in pending:
        if intent_routing:
            with inbox.connection(config, binding) as db, database.transaction(db):
                # Do not take over a prompt prepared concurrently, even if still unsent.
                if db.execute(
                    "SELECT 1 FROM wx_confirmation_prompts WHERE batch_id=?", (row["id"],)
                ).fetchone():
                    continue
                intent = intents.plan_locked(db, binding, row["id"])
                view = batches.snapshot(db, binding, row["id"])
                if intent and intent["auto"] and view["content_ready"]:
                    batches.choose_in_transaction(db, binding, row["id"], intent["action"])
                    continue
        prepare(config, binding, row["id"])
    return views
