"""Finite work for one service role; original jobs and authorization boundaries are reused."""

from ... import automatic_ingestion
from ...config import Config, InvalidInput
from .. import dispatch, intake_extraction, intake_processing, intake_repository
from . import policy


def tick(config: Config, identity: str, role: str) -> bool:
    channel, _ = intake_repository.channel(config, identity)
    if channel["state"] not in {"running", "paused", "stopping", "error"}:
        return False
    if role == "query":
        dispatch.advance(config, identity, channel["revision"], independent=True)
        return True
    if channel["value"].get("expert_enabled"):
        from ... import database, expert_feishu
        from .. import configuration, expert, store

        def guard() -> None:
            with store.readonly(config) as side, database.connect(config.state_root) as main:
                if side is None:
                    raise InvalidInput("channel_service_binding_unavailable")
                current = store.get(side, identity)
                if not policy.intake_allowed(config, current):
                    raise InvalidInput("channel_service_binding_unavailable")
                store.authorize(config, main, current)

        service = policy.get(config, identity, "intake")
        if service is None:
            raise InvalidInput("channel_services_activation_required")
        expert_feishu.dispatch_one(
            config,
            configuration.feishu_settings(config, channel).binding,
            expert.authorization(identity, channel["revision"]),
            guard=guard,
            minimum_rowid=service.get("expert_watermark", 0),
            channel_id=identity,
        )
    intake_processing.advance(config, identity)
    captured = intake_extraction.advance(config, identity)
    applied = automatic_ingestion.tick(
        config, channel_id=identity, admit_after=policy.cutoff(config, identity)
    )
    return bool(captured or applied)


def recover(config: Config, identity: str, role: str) -> None:
    """Only the process holding this role lease may diagnose its interrupted claims."""
    if role != "intake":
        return
    import json

    from ... import database

    with database.locked(config), database.connect(config.state_root) as db:
        for row in db.execute(
            "SELECT id,value,created_at FROM automatic_jobs WHERE state='claimed'"
        ).fetchall():
            value = json.loads(row["value"])
            owner = value.get("intake", {}).get("channel") or value.get("channel_batch", {}).get(
                "channel"
            )
            if owner == identity and row["created_at"] >= policy.cutoff(config, identity):
                db.execute(
                    "UPDATE automatic_jobs SET state='reconcile',"
                    "reason='automatic_interrupted_claim' WHERE id=?",
                    (row["id"],),
                )
