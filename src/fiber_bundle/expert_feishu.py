"""Explicit owner Feishu task IDs; no nearest-file guessing or source-text commands."""

import json
import re
from collections.abc import Callable
from typing import Any

from . import database, expert_dispatch, expert_tasks, source_imports, weixin_inbox
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding

PATTERN = re.compile(r"/fake-expert (start|resume) ([0-9a-f-]{36})(?: workpack=([0-9a-f-]{36}))?")


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
    from .note_changes import identifier

    identifier(authorization)
    if type(maximum) is not int or not (0 if unlimited else 1) <= maximum <= 100:
        raise InvalidInput("expert_feishu_explicit_budget_required")
    from .expert_file_intents import capture as capture_file_intent

    if capture_file_intent(
        config, binding, message, authorization, maximum, guard=guard, unlimited=unlimited
    ):
        return True
    if message.get("forwarded") or message.get("from_user_id") != binding.owner_id:
        return False
    raw = message["fiber_feishu"]["raw_event"]["event"]["message"]
    if raw.get("message_type") != "text":
        return False
    body = json.loads(raw["content"]).get("text", "")
    if not isinstance(body, str) or not body.startswith("/fake-expert"):
        return False
    match = PATTERN.fullmatch(body.strip())
    state = "queued" if match else "needs_selection"
    selected = {"action": match[1], "id": match[2], "workpack": match[3]} if match else {}
    for value in selected.values():
        if value is not None and value not in {"start", "resume"}:
            identifier(value)
    wire = canonical(
        {
            "selected": selected,
            "binding": binding.__dict__,
            "maximum": maximum,
            "authorization": authorization,
            "message_sha256": digest(canonical(message).encode()),
        }
    )
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        if guard is not None:
            guard()
        weixin_inbox.bound(db, binding)
        from .expert_command_schema import initialize

        initialize(db)
        old = db.execute(
            "SELECT request_json FROM fiber_expert_commands WHERE authorization=? AND event_id=?",
            (authorization, message["message_id"]),
        ).fetchone()
        if old is not None:
            if old[0] != wire:
                raise InvalidInput("expert_feishu_command_changed")
            return True
        configs = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT request_json FROM fiber_expert_commands WHERE authorization=?",
                (authorization,),
            )
        ]
        if any(
            value["binding"] != binding.__dict__ or value["maximum"] != maximum for value in configs
        ):
            raise InvalidInput("expert_feishu_authorization_changed")
        if not unlimited and (maximum < 1 or len(configs) >= maximum):
            raise InvalidInput("expert_feishu_budget_exhausted")
        db.execute(
            "INSERT INTO fiber_expert_commands VALUES(?,?,?,?,NULL)",
            (authorization, message["message_id"], wire, state),
        )
    return True


def dispatch_one(
    config: Config,
    binding: Binding,
    authorization: str,
    *,
    guard: Callable[[], None] | None = None,
    minimum_rowid: int = 0,
    channel_id: str | None = None,
) -> dict[str, Any] | None:
    from .expert_file_dispatch import dispatch as dispatch_file

    selected = dispatch_file(
        config, binding, authorization, guard=guard, minimum_rowid=minimum_rowid
    )
    if selected is not None:
        return selected
    with weixin_inbox.connection(config, binding) as journal, database.transaction(journal):
        if guard is not None:
            guard()
        weixin_inbox.bound(journal, binding)
        if not journal.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fiber_expert_commands'"
        ).fetchone():
            return None
        row = journal.execute(
            "SELECT * FROM fiber_expert_commands WHERE "
            "authorization=? AND state='queued' AND rowid>? ORDER BY rowid "
            "LIMIT 1",
            (authorization, minimum_rowid),
        ).fetchone()
        if row is None:
            return None
        journal.execute(
            "UPDATE fiber_expert_commands SET state='dispatching' "
            "WHERE authorization=? AND event_id=?",
            (authorization, row["event_id"]),
        )
    request = json.loads(row["request_json"])
    command = request["selected"]
    try:
        if request["binding"] != binding.__dict__:
            raise InvalidInput("expert_feishu_scope_changed")
        with database.locked(config):
            if guard is not None:
                guard()
            db = database.connect(config.state_root)
            try:
                source, _, _ = expert_tasks.authorize(config, db, binding.client_id, command["id"])
                if source["vault_id"] != binding.vault_id:
                    raise InvalidInput("expert_feishu_vault_mismatch")
                if command["action"] == "resume":
                    result = expert_dispatch.resume(
                        config, db, binding.client_id, command["id"], command["workpack"]
                    )
                else:
                    if command["workpack"] is not None:
                        raise InvalidInput("expert_feishu_start_workpack_frozen")
                    result = expert_dispatch.launch(config, db, binding.client_id, command["id"])
                source_imports.get(config, db, binding.client_id, command["id"])
                from . import automatic_jobs

                automatic_id = automatic_jobs.enqueue(
                    config,
                    db,
                    binding.client_id,
                    binding.vault_id,
                    "expert",
                    command["id"],
                    {"channel_batch": {"channel": channel_id, "batch": row["event_id"]}}
                    if channel_id
                    else None,
                )
                if automatic_id:
                    automatic_job = automatic_jobs.get(db, automatic_id)
                    if automatic_job["state"] == "queued" and automatic_job["phase"] == "extract":
                        automatic_jobs.settle(db, automatic_job, "waiting", "review")
            finally:
                db.close()
    except Exception:
        _settle(config, binding, authorization, row["event_id"], "failed", command["id"])
        raise
    _settle(config, binding, authorization, row["event_id"], "submitted", command["id"])
    return result


def _settle(
    config: Config, binding: Binding, authorization: str, event_id: str, state: str, task: str
) -> None:
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        db.execute(
            "UPDATE fiber_expert_commands SET state=?,task_id=? "
            "WHERE authorization=? AND event_id=?",
            (state, task, authorization, event_id),
        )


def listing(config: Config, db: Any, client_id: str, vault_id: str) -> list[dict[str, Any]]:
    from . import admin_batches, auth

    auth.grant(db, client_id, vault_id, "add")
    auth.grant(db, client_id, vault_id, "original")
    with admin_batches.journal(config) as journal:
        if (
            journal is None
            or not journal.execute(
                "SELECT 1 FROM sqlite_master WHERE name='fiber_expert_commands'"
            ).fetchone()
        ):
            return []
        rows = journal.execute(
            "SELECT event_id,state,task_id,request_json FROM fiber_expert_commands "
            "WHERE json_extract(request_json,'$.binding.client_id')=? "
            "AND json_extract(request_json,'$.binding.vault_id')=? ORDER BY rowid DESC LIMIT 100",
            (client_id, vault_id),
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "state": row["state"],
                "task_id": row["task_id"],
                "next_action": "请在本人私聊明确指定任务UUID；多个附件或关系不明时先在GUI选择来源。"
                if row["state"] == "needs_selection"
                else "在对应导入任务查看结果；失败或未知不会自动重放。",
            }
            for row in rows
        ]
