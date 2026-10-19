"""Bounded expert control acknowledgements; a claimed or unknown send is never replayed."""

import json
import time
from collections.abc import Callable
from typing import Any

from . import database, feishu_budgets, feishu_sync, weixin_inbox
from .config import Config
from .inputs import canonical
from .weixin_events import Binding


def body(state: str, value: dict[str, Any]) -> str:
    intent = value["intent"]
    if state == "waiting_file":
        remaining = max(0, int(intent["expires_at"] - time.time()))
        return f"请在本私聊直接发送一个 PDF（剩余 {remaining} 秒），或发送“取消fake-expert”。"
    if state == "needs_selection":
        return "请选择本次文件，发送“选择文件 序号”：\n" + "\n".join(
            f"{i + 1}. {f['name']}" for i, f in enumerate(intent.get("candidates", []))
        )
    if state in {"awaiting_download", "bound"}:
        return "已定位本次文件，等待下载和摘要校验；尚未启动专家处理。"
    if state in {"queued", "submitted"}:
        return (
            "专家请求已冻结，任务 "
            + str(value.get("selected", {}).get("id", "待准备"))
            + "。仅生成完整 Draft；独立语义审核仍待完成。"
        )
    return {
        "expired": "等待文件已到期，请重新发送指令。",
        "cancelled": "等待意图已取消；已完成的资料和费用记录保留。",
        "failed": "专家处理未启动或需核对，请在应用中查看；不会自动重试。",
    }.get(str(state), "专家文件状态需要核对。")


def take(
    config: Config,
    binding: Binding,
    authorization: str,
    guard: Callable[[], None],
    *,
    unlimited: bool = False,
    minimum_rowid: int = 0,
) -> tuple[str, str, str] | None:
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        guard()
        if db.execute(
            "SELECT 1 FROM fiber_expert_commands WHERE authorization=? "
            "AND json_extract(request_json,'$.intent.ack.state') IN ('sending','unknown')",
            (authorization,),
        ).fetchone():
            return None
        for row in db.execute(
            "SELECT * FROM fiber_expert_commands WHERE authorization=? AND rowid>? ORDER BY rowid",
            (authorization, minimum_rowid),
        ).fetchall():
            value = json.loads(row["request_json"])
            if "intent" not in value:
                continue
            prior = value["intent"].get("ack", {})
            if prior.get("for_state") == row["state"]:
                continue
            if not feishu_budgets.reserve_locked(
                db,
                binding,
                authorization,
                "outbound",
                "expert:" + row["event_id"] + ":" + row["state"],
                unlimited=unlimited,
            ):
                return None
            value["intent"]["ack"] = {"for_state": row["state"], "state": "sending"}
            db.execute(
                "UPDATE fiber_expert_commands SET request_json=? WHERE"
                " authorization=? AND event_id=?",
                (canonical(value), authorization, row["event_id"]),
            )
            return row["event_id"], row["state"], body(row["state"], value)
    return None


async def deliver(
    config: Config,
    binding: Binding,
    authorization: str,
    sender: Callable[..., Any],
    guard: Callable[[], None],
    *,
    unlimited: bool = False,
    minimum_rowid: int = 0,
) -> None:
    selected = await feishu_sync.run(
        take,
        config,
        binding,
        authorization,
        guard,
        unlimited=unlimited,
        minimum_rowid=minimum_rowid,
    )
    if selected is None:
        return
    event, for_state, message = selected
    state = "unknown"
    try:
        state = "sent" if await sender(binding.owner_id, message, guard) else "unknown"
    finally:
        with weixin_inbox.connection(config, binding) as db, database.transaction(db):
            row = db.execute(
                "SELECT request_json FROM fiber_expert_commands WHERE"
                " authorization=? AND event_id=?",
                (authorization, event),
            ).fetchone()
            value = json.loads(row[0])
            if value["intent"]["ack"]["for_state"] == for_state:
                value["intent"]["ack"]["state"] = state
                db.execute(
                    "UPDATE fiber_expert_commands SET request_json=? WHERE"
                    " authorization=? AND event_id=?",
                    (canonical(value), authorization, event),
                )
