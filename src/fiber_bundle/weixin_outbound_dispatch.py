"""Bounded automatic delivery using existing ledgers, without retrying uncertain effects."""

from collections.abc import Awaitable, Callable

from . import batch_reminders
from . import weixin_inbox as inbox
from . import weixin_prompt_delivery as prompts
from . import weixin_receipt_delivery as receipts
from . import weixin_result_queue as result_queue
from . import weixin_task_delivery as task_delivery
from .config import Config, InvalidInput
from .weixin_delivery_state import ensure_resolved as ensure_resolved
from .weixin_events import Binding


def limit(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise InvalidInput("outbound limit must be an integer from 0 to 100")
    return value


async def drain(
    config: Config,
    binding: Binding,
    sender: Callable[[str, str], Awaitable[bool]],
    *,
    maximum: int,
    result_sender: task_delivery.Sender | None = None,
    reminder_sender: task_delivery.Sender | None = None,
) -> int:
    """Called under the receiver lock. Receipts first, then still-relevant frozen prompts.

    Any uncertain prior attempt blocks the account, including after restart. Ledger
    claiming also protects against a concurrent explicit sender of the same item.
    """
    if limit(maximum) == 0:
        return 0
    receipts.initialize(config, binding)
    sent = 0
    for _ in range(maximum):
        with inbox.connection(config, binding) as db:
            inbox.bound(db, binding)
            ensure_resolved(db, binding)
            receipt = db.execute(
                "SELECT e.job_id FROM wx_command_receipts c JOIN events e "
                "ON c.account_id=e.account_id AND c.event_id=e.event_id "
                "LEFT JOIN wx_receipt_deliveries d ON d.job_id=e.job_id "
                "WHERE c.account_id=? AND c.kind='command' "
                "AND (d.state IS NULL OR d.state='pending') ORDER BY e.rowid LIMIT 1",
                (binding.account_id,),
            ).fetchone()
            terminology = None
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='wx_media_terminology_prompts'"
            ).fetchone():
                terminology = db.execute(
                    "SELECT id FROM wx_media_terminology_prompts "
                    "WHERE account_id=? AND state='pending' ORDER BY rowid LIMIT 1",
                    (binding.account_id,),
                ).fetchone()
            prompt = db.execute(
                "SELECT p.id FROM wx_confirmation_prompts p JOIN wx_batches b ON b.id=p.batch_id "
                "WHERE p.account_id=? AND p.state='pending' AND b.state='frozen' "
                "ORDER BY p.rowid LIMIT 1",
                (binding.account_id,),
            ).fetchone()
        if receipt is not None:
            result = await receipts.deliver(config, binding, receipt["job_id"], sender)
        elif prompt is not None:
            result = await prompts.deliver(config, binding, prompt["id"], sender)
        elif terminology is not None and result_sender is not None:
            from . import weixin_terminology_delivery

            result = await weixin_terminology_delivery.deliver(
                config, binding, terminology["id"], result_sender
            )
        elif result_sender is not None:
            batch_id = result_queue.next_batch(config, binding)
            if batch_id is not None:
                result = await task_delivery.deliver(config, binding, batch_id, result_sender)
            elif reminder_sender is not None:
                result = await batch_reminders.deliver(config, binding, reminder_sender)
                if result["state"] == "idle":
                    break
            else:
                break
        elif reminder_sender is not None:
            result = await batch_reminders.deliver(config, binding, reminder_sender)
            if result["state"] == "idle":
                break
        else:
            break
        if result["state"] == "obsolete":
            continue
        if result["state"] != "sent":
            raise InvalidInput("outbound delivery not confirmed")
        sent += 1
    return sent
