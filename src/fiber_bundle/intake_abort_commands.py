"""Capture owner control messages before material intake, independent of query routing."""

import json
import time
from collections.abc import Callable
from typing import Any

from . import database, intake_abort_scope, intake_abort_store, weixin_inbox
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding


def capture(
    config: Config,
    binding: Binding,
    message: dict[str, Any],
    guard: Callable[[], None] | None = None,
) -> bool:
    raw = message.get("fiber_feishu", {}).get("raw_event", {})
    if raw.get("event", {}).get("message", {}).get("message_type") != "text":
        return False
    items = message.get("item_list", [])
    if len(items) != 1 or items[0].get("type") != 1:
        return False
    text = items[0]["text_item"]["text"]
    recognized, target = intake_abort_store.parse(text)
    if not recognized:
        return False
    event = message["message_id"]
    command_sha = digest(text.encode())
    with weixin_inbox.connection(config, binding, leased=True) as db:
        weixin_inbox.bound(db, binding)
        previous = db.execute(
            "SELECT command_sha256 FROM fiber_intake_abort_commands "
            "WHERE account_id=? AND event_id=?",
            (binding.account_id, event),
        ).fetchone()
        if previous:
            if previous[0] != command_sha:
                raise InvalidInput("intake_abort_event_changed")
            return True
    result: dict[str, Any] = (
        {"state": "invalid"}
        if target == "invalid"
        else intake_abort_scope.resolve(config, binding, target)
    )
    scope = result.get("scope", {})
    if guard:
        guard()
    with intake_abort_store.critical(config):
        with weixin_inbox.connection(config, binding, leased=True) as db, database.transaction(db):
            now = time.time()
            db.execute(
                "INSERT OR IGNORE INTO fiber_intake_abort_commands VALUES(?,?,?,?,?,?,NULL,?,?)",
                (
                    binding.account_id,
                    event,
                    command_sha,
                    result["state"],
                    canonical(scope),
                    canonical(result),
                    now,
                    now,
                ),
            )
    return True


def response(value: dict[str, Any]) -> str:
    state = value["state"]
    scope = value.get("scope", {})
    identity = scope.get("job_id") or scope.get("batch", "")
    if state == "requested":
        return f"已记录任务 {identity} 的终止请求，正在停止提取；不再发布后续产物。"
    if state == "stopped":
        return f"任务 {identity} 已取消后续提取和发布。原件、已入库笔记和已提交调用的费用保留。"
    if state == "too_late":
        return f"任务 {identity} 已提交入库，无法通过 abort 撤销；现有笔记未删除。"
    if state == "ambiguous":
        return "存在多个任务，请发送 abort <任务编号>：\n" + "\n".join(value.get("candidates", []))
    if state == "invalid":
        return "用法：abort，或 abort <完整任务编号/唯一的前8位编号>。"
    return "当前入口没有匹配的进行中任务；请核对本入口回执中的任务编号。"


def advance(config: Config, binding: Binding) -> None:
    from .intake_abort_tasks import stop

    with weixin_inbox.connection(config, binding) as db:
        row = db.execute(
            "SELECT * FROM fiber_intake_abort_commands WHERE account_id=? "
            "AND state='requested' ORDER BY created_at LIMIT 1",
            (binding.account_id,),
        ).fetchone()
    if row is None:
        return
    try:
        result = stop(config, binding, json.loads(row["scope"]))
    except Exception as error:
        result = {**json.loads(row["value"]), "stop_error": type(error).__name__}
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        db.execute(
            "UPDATE fiber_intake_abort_commands SET state=?,value=?,updated_at=? "
            "WHERE account_id=? AND event_id=?",
            (result["state"], canonical(result), time.time(), binding.account_id, row["event_id"]),
        )


async def deliver(config: Config, binding: Binding, sender: Any, guard: Any) -> None:
    from . import feishu_sync

    def take() -> dict[str, Any] | None:
        with weixin_inbox.connection(config, binding) as db, database.transaction(db):
            row = db.execute(
                "SELECT * FROM fiber_intake_abort_commands WHERE account_id=? "
                "AND receipt_state IS NULL ORDER BY created_at LIMIT 1",
                (binding.account_id,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE fiber_intake_abort_commands SET receipt_state='sending' "
                "WHERE account_id=? AND event_id=?",
                (binding.account_id, row["event_id"]),
            )
            return dict(row)

    try:
        row = await feishu_sync.run(take)
    except database.LockTimeout:
        # The first lease was not acquired: nothing was claimed or sent. Poll later.
        return
    if row is None:
        return
    state = "unknown"
    try:
        state = (
            "sent"
            if await sender(binding.owner_id, response(json.loads(row["value"])), guard)
            else "unknown"
        )
    finally:

        def settle() -> None:
            with weixin_inbox.connection(config, binding) as db:
                db.execute(
                    "UPDATE fiber_intake_abort_commands SET receipt_state=?,updated_at=? "
                    "WHERE account_id=? AND event_id=?",
                    (state, time.time(), binding.account_id, row["event_id"]),
                )

        await feishu_sync.run(settle)
