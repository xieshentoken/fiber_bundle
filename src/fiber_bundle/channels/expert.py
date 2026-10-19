"""Explicit expert-command capability for the project-owned Feishu channel worker."""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import expert_feishu, expert_intent_receipts, feishu_budgets, feishu_receiver, feishu_sync
from ..config import Config
from . import configuration, dispatch, feishu


def authorization(identity: str, revision: int) -> str:
    return str(uuid5(NAMESPACE_URL, "fiber-channel:" + identity + ":" + str(revision)))


async def tick(config: Config, channel: dict[str, Any], adapter: Any) -> None:
    if not channel["value"].get("expert_enabled"):
        return
    from .services import policy as service_policy

    independent = service_policy.get(config, channel["id"], "intake")
    minimum_rowid = independent.get("expert_watermark", 0) if independent else 0
    binding = configuration.feishu_settings(config, channel).binding
    grant = authorization(channel["id"], channel["revision"])

    def guard() -> None:
        dispatch.archival_guard(config, channel["id"], channel["revision"])

    def eligible(db: Any, job: str) -> bool:
        row = db.execute("SELECT event_id FROM events WHERE job_id=?", (job,)).fetchone()
        return bool(
            row
            and db.execute(
                "SELECT 1 FROM fiber_expert_commands WHERE authorization=? "
                "AND rowid>? AND state IN ('awaiting_download','bound','needs_selection') AND "
                "(json_extract(request_json,'$.intent.selected_file.event_id')=? OR "
                "json_extract(request_json,'$.intent.reply_message_id')=? OR EXISTS "
                "(SELECT 1 FROM json_each(request_json,'$.intent.candidates') "
                "WHERE json_extract(value,'$.event_id')=?))",
                (grant, minimum_rowid, row[0], row[0], row[0]),
            ).fetchone()
        )

    def reserve(db: Any, target: str) -> bool:
        guard()
        return feishu_budgets.reserve_locked(
            db,
            binding,
            grant,
            "download",
            target,
            unlimited=channel["value"].get("policy_version", 2) == 2,
        )

    await feishu_receiver.download_one(
        config, binding, adapter, reserve=reserve, eligible=eligible, guard=guard
    )
    if independent is None:
        await feishu_sync.run(expert_feishu.dispatch_one, config, binding, grant, guard=guard)

    async def sender(owner: str, message: str, checked: Any) -> bool:
        return await feishu.send(adapter, owner, message, "", checked)

    await expert_intent_receipts.deliver(
        config,
        binding,
        grant,
        sender,
        guard,
        unlimited=channel["value"].get("policy_version", 2) == 2,
        minimum_rowid=minimum_rowid,
    )
