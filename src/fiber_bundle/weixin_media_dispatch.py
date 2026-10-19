"""Opt-in frozen-batch media discovery; bounded attempts, no automatic retries."""

from collections.abc import Awaitable, Callable
from typing import Any

from . import weixin_batches as batches
from . import weixin_confirmations as confirmations
from . import weixin_download as download
from . import weixin_inbox as inbox
from . import weixin_media_queue as queue
from . import weixin_media_worker as worker
from .config import Config, IntegrityError, InvalidInput
from .weixin_events import Binding


def limit(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise InvalidInput("media dispatch limit must be an integer from 0 to 100")
    return value


def _scope(config: Config, binding: Binding, batch_id: str, expected: str | None = None) -> str:
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        view = batches.snapshot(db, binding, batch_id)
        if view["state"] not in {"frozen", "selected"}:
            raise InvalidInput("media dispatch requires an active frozen batch")
        scope = confirmations.members(db, binding, batch_id)
        if expected is not None and scope != expected:
            raise IntegrityError("media dispatch scope changed")
        prompt = db.execute(
            "SELECT members_json FROM wx_confirmation_prompts WHERE account_id=? AND batch_id=?",
            (binding.account_id, batch_id),
        ).fetchone()
        if prompt is not None and prompt[0] != scope:
            raise IntegrityError("media confirmation scope changed")
        return scope


def _guarded_fetcher(
    config: Config,
    binding: Binding,
    batch_id: str,
    scope: str,
    fetcher: Callable[[str], Awaitable[bytes]],
) -> Callable[[str], Awaitable[bytes]]:
    async def fetch(url: str) -> bytes:
        _scope(config, binding, batch_id, scope)
        data = await fetcher(url)
        _scope(config, binding, batch_id, scope)
        return data

    return fetch


async def drain(
    config: Config,
    binding: Binding,
    *,
    maximum: int = 0,
    after: int = 0,
    fetcher: Callable[[str], Awaitable[bytes]] = download.fetch,
) -> dict[str, Any]:
    """Scan at most 100 source events and attempt at most maximum missing items.

    next_cursor is an ephemeral scan hint, never an acknowledgement. Keep it between
    polls; restart from zero safely. A partial event is revisited until complete.
    Only exact frozen members are eligible, not unclassified or manual queued jobs.
    Failed/interrupted/running work requires explicit retry/reconcile; never recreated.
    """
    budget = limit(maximum)
    if type(after) is not int or after < 0:
        raise InvalidInput("invalid media discovery cursor")
    result: dict[str, Any] = {"state": "idle", "processed": 0, "attempts": [], "next_cursor": 0}
    if budget == 0:
        return result
    queue.initialize(config, binding)
    with inbox.connection(config, binding) as db:
        inbox.bound(db, binding)
        rows = db.execute(
            "SELECT e.rowid AS cursor,e.job_id,m.batch_id FROM events e "
            "JOIN wx_batch_members m ON m.account_id=e.account_id AND m.event_id=e.event_id "
            "JOIN wx_batches b ON b.id=m.batch_id AND b.account_id=e.account_id "
            "JOIN wx_command_receipts c ON c.account_id=e.account_id AND c.event_id=e.event_id "
            "WHERE e.account_id=? AND e.rowid>? AND e.state='incomplete' "
            "AND c.kind='source' AND b.state IN ('frozen','selected') "
            "ORDER BY e.rowid LIMIT 101",
            (binding.account_id, after),
        ).fetchall()
    for row in rows[:100]:
        scope = _scope(config, binding, row["batch_id"])
        view = batches.get(config, binding, row["batch_id"])
        # Unknown types cannot become a supported source simply by downloading media.
        if any(member["state"] == "unsupported" for member in view["members"]):
            continue
        tasks = queue.enqueue(config, binding, row["job_id"], maximum=budget - result["processed"])
        for task in tasks:
            if task["state"] != "queued":
                result.update(state="recovery_required", next_cursor=row["cursor"] - 1)
                result["attempts"].append(task)
                return result

            completed = await worker.dispatch(
                config,
                binding,
                fetcher=_guarded_fetcher(config, binding, row["batch_id"], scope, fetcher),
                attempt_id=task["id"],
            )
            result["processed"] += 1
            result["attempts"].extend(completed["attempts"])
            if completed["state"] not in {"idle", "limit_reached"}:
                result.update(state=completed["state"], next_cursor=row["cursor"] - 1)
                if "unsettled_attempt" in completed:
                    result["unsettled_attempt"] = completed["unsettled_attempt"]
                return result
            if result["processed"] == budget:
                result.update(state="limit_reached", next_cursor=row["cursor"] - 1)
                return result
    if len(rows) > 100:
        result.update(state="page_complete", next_cursor=rows[99]["cursor"])
    return result
