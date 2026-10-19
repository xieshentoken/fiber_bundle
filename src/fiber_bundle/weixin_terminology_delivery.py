"""Once-only explicit terminology notification using the shared account uncertainty barrier."""

import asyncio
from contextlib import closing
from typing import Any

from . import database, media_glossary, media_jobs
from . import weixin_inbox as inbox
from . import weixin_media_terminology as terminology
from .config import Config, InvalidInput
from .weixin_delivery_state import ensure_resolved
from .weixin_events import Binding
from .weixin_task_delivery import Sender


async def deliver(config: Config, binding: Binding, nonce: str, sender: Sender) -> dict[str, Any]:
    with (
        inbox.connection(config, binding) as db,
        closing(database.connect(config.state_root)) as main,
    ):
        inbox.bound(db, binding)
        prompt = db.execute(
            "SELECT * FROM wx_media_terminology_prompts WHERE account_id=? AND id=?",
            (binding.account_id, nonce),
        ).fetchone()
        if prompt is None:
            raise InvalidInput("media_terminology_prompt_missing")
        if prompt["state"] != "pending":
            return {"prompt_id": nonce, "state": prompt["state"]}
        ensure_resolved(db, binding)
        try:
            terminology.checked(config, main, binding, prompt)
            job = media_jobs.load(main, binding.client_id, prompt["job_id"])
            if media_glossary.gate(main, job)["confirmation_recorded"]:
                raise InvalidInput("media_terminology_already_confirmed")
        except InvalidInput as error:
            if str(error) not in {
                "media_terminology_prompt_stale",
                "media_glossary_task_stopped",
                "media_terminology_already_confirmed",
            }:
                raise
            db.execute(
                "UPDATE wx_media_terminology_prompts SET state='obsolete' "
                "WHERE id=? AND state='pending'",
                (nonce,),
            )
            return {"prompt_id": nonce, "state": "obsolete"}
        body = prompt["body"]
        db.execute("UPDATE wx_media_terminology_prompts SET state='sending' WHERE id=?", (nonce,))
    state = "unknown"
    checked_once = False

    def authorize() -> None:
        nonlocal checked_once
        if checked_once:
            raise InvalidInput("media_terminology_transport_no_retry")
        with (
            inbox.connection(config, binding) as live,
            closing(database.connect(config.state_root)) as current,
        ):
            inbox.bound(live, binding)
            saved = live.execute(
                "SELECT * FROM wx_media_terminology_prompts WHERE account_id=? AND id=?",
                (binding.account_id, nonce),
            ).fetchone()
            if saved is None or saved["state"] != "sending" or saved["body"] != body:
                raise InvalidInput("media_terminology_delivery_changed")
            terminology.checked(config, current, binding, saved)
            job = media_jobs.load(current, binding.client_id, saved["job_id"])
            if media_glossary.gate(current, job)["confirmation_recorded"]:
                raise InvalidInput("media_terminology_already_confirmed")
        checked_once = True

    try:
        async with asyncio.timeout(30):
            if await sender(binding.owner_id, body, authorize) is True and checked_once:
                state = "sent"
    except asyncio.CancelledError:
        raise
    except Exception:
        pass  # Remote text may contain secrets; persist only the uncertain category.
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        db.execute(
            "UPDATE wx_media_terminology_prompts SET state=? WHERE id=? AND state='sending'",
            (state, nonce),
        )
    return {"prompt_id": nonce, "state": state}
