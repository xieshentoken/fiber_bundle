"""Hot-read shared intake policy in either project-owned Feishu receiver."""

import asyncio
from typing import Any

from .. import auth, feishu_sync
from ..config import Config, InvalidInput
from . import intake_processing, intake_repository, store


def owns_batch(config: Config, identity: str | None, batch: str) -> bool:
    if identity is None:
        return False
    with store.readonly(config) as db:
        return bool(
            db is not None
            and intake_repository.existing(db)
            and db.execute(
                "SELECT 1 FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                (identity, batch),
            ).fetchone()
        )


async def tick(config: Config, identity: str | None, adapter: Any) -> str:
    if identity is None:
        return "unconfigured"
    from ..vault_relocation.guard import paused, selected_channel

    vault = await feishu_sync.run(selected_channel, config, identity)
    is_paused = await asyncio.to_thread(paused, config, vault) if vault else False
    if is_paused:
        return "intake_waiting_for_vault_relocation"
    _, policy = await feishu_sync.run(intake_repository.channel, config, identity)
    from .services import policy as service_policy

    independent = await feishu_sync.run(service_policy.get, config, identity, "intake")
    if independent is not None:
        from . import intake_receipts

        try:
            await intake_receipts.deliver(config, identity, adapter)
            await intake_processing.download(config, identity, adapter)
        except auth.Denied:
            return "intake_permission_unavailable"
        except InvalidInput:
            return "intake_requires_review"
        return "ready" if independent["enabled"] else "off"
    if policy["revision"] == 0:
        return "unconfigured"
    try:
        await feishu_sync.run(intake_processing.advance, config, identity)
        from . import intake_extraction, intake_receipts

        await intake_receipts.deliver(config, identity, adapter)
        await feishu_sync.run(intake_extraction.advance, config, identity)
        await intake_receipts.deliver(config, identity, adapter)
        await intake_processing.download(config, identity, adapter)
        await feishu_sync.run(intake_processing.advance, config, identity)
    except auth.Denied:
        return "intake_permission_unavailable"
    except InvalidInput:
        # Per-operation ledgers preserve failed/unknown work. Do not re-launch it.
        return "intake_requires_review"
    from .intake_health import pending_review

    if policy["settings"]["enabled"] and await feishu_sync.run(pending_review, config, identity):
        return "intake_requires_review"
    return "ready" if policy["settings"]["enabled"] else "off"
