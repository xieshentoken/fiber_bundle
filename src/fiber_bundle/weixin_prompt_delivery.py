"""Explicit prompt delivery boundary. Unknown outcomes never trigger automatic resend."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from . import database
from . import weixin_batches as batches
from . import weixin_confirmations as confirmations
from . import weixin_inbox as inbox
from .config import Config, IntegrityError, InvalidInput
from .weixin_events import Binding


async def deliver(
    config: Config,
    binding: Binding,
    prompt_id: str,
    sender: Callable[[str, str], Awaitable[bool]],
) -> dict[str, Any]:
    """Trusted sender must be bound to this account. No installed transport is called here."""
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        row = db.execute(
            "SELECT * FROM wx_confirmation_prompts WHERE account_id=? AND id=?",
            (binding.account_id, prompt_id),
        ).fetchone()
        if row is None:
            raise InvalidInput("unknown confirmation prompt")
        if row["state"] != "pending":
            return {"prompt_id": prompt_id, "state": row["state"]}
        if row["members_json"] != confirmations.members(db, binding, row["batch_id"]):
            raise IntegrityError("confirmation scope changed")
        if batches.snapshot(db, binding, row["batch_id"])["state"] != "frozen":
            raise InvalidInput("confirmation is no longer pending")
        body = row["body"]
        db.execute("UPDATE wx_confirmation_prompts SET state='sending' WHERE id=?", (prompt_id,))
    state = "unknown"
    try:
        async with asyncio.timeout(30):
            acknowledged = await sender(binding.owner_id, body)
        if acknowledged is True:
            state = "sent"
    except asyncio.CancelledError:
        # Durable sending remains an unresolved external effect; never auto resend.
        raise
    except Exception:
        pass  # Only categorical unknown is retained; third-party text may contain secrets.
    with inbox.connection(config, binding) as db, database.transaction(db):
        inbox.bound(db, binding)
        db.execute(
            "UPDATE wx_confirmation_prompts SET state=? WHERE id=? AND state='sending'",
            (state, prompt_id),
        )
    return {"prompt_id": prompt_id, "state": state}
