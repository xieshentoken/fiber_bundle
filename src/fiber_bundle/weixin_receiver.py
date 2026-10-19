"""Journal-first Hermes receiver with opt-in bounded delivery and no Agent dispatch."""

import asyncio
import fcntl
import os
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from . import weixin_batches as batches
from . import weixin_confirmations as confirmations
from . import weixin_inbox as inbox
from . import weixin_media_dispatch as media_dispatch
from . import weixin_outbound_dispatch as outbound
from . import weixin_store_dispatch as store_dispatch
from . import weixin_task_submission as task_submission
from .article_worker import CapturePolicy
from .config import Config, InvalidInput, contained
from .execution_config import ExecutionConfig
from .weixin_events import Binding


@contextmanager
def receiver_lock(config: Config) -> Iterator[None]:
    """One cooperating receiver per state root, held across network waits."""
    config.validate()
    fd = os.open(
        contained(config.state_root, "weixin-receiver.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("weixin receiver already active") from None
        yield
    finally:
        os.close(fd)


def configure(
    adapter: Any,
    config: Config,
    binding: Binding,
    *,
    quiet_seconds: float = 3,
    outbound_limit: int = 0,
    store_limit: int = 0,
    result_receipts: bool = False,
    submission_limit: int = 0,
    execution_policy: ExecutionConfig | None = None,
    capture_policy: CapturePolicy | None = None,
    intent_routing: bool = False,
    media_limit: int = 0,
) -> None:
    """Trusted local bootstrap only; binding must have been explicitly initialized."""
    if getattr(adapter, "_fiber_capture_version", None) != 1:
        raise InvalidInput("reviewed Hermes capture candidate required")
    if adapter._running or getattr(adapter, "_poll_task", None) is not None:
        raise InvalidInput("weixin receiver must be stopped before configuration")
    if adapter._account_id != binding.account_id:
        raise InvalidInput("weixin account binding mismatch")
    quiet = batches.seconds(quiet_seconds)
    maximum = outbound.limit(outbound_limit)
    store_maximum = store_dispatch.limit(store_limit)
    submission_maximum = task_submission.limit(submission_limit)
    media_maximum = media_dispatch.limit(media_limit)
    if type(intent_routing) is not bool:
        raise InvalidInput("intent routing switch must be boolean")
    if submission_maximum and not isinstance(execution_policy, ExecutionConfig):
        raise InvalidInput("task submission requires an explicit execution policy")
    if execution_policy is not None:
        execution_policy.validate()
    if capture_policy is not None:
        capture_policy.validate()
    if maximum and not callable(getattr(adapter, "_fiber_send_once", None)):
        raise InvalidInput("reviewed Hermes sender required")
    if type(result_receipts) is not bool:
        raise InvalidInput("result receipts switch must be boolean")
    if result_receipts and (
        not maximum or not callable(getattr(adapter, "_fiber_send_result_once", None))
    ):
        raise InvalidInput("result receipts require outbound budget and guarded sender")
    inbox.cursor(config, binding)
    confirmations.initialize(config, binding)
    adapter._fiber_quiet_seconds = quiet
    adapter._fiber_outbound_limit = maximum
    adapter._fiber_store_limit = store_maximum
    adapter._fiber_result_receipts = result_receipts
    adapter._fiber_submission_limit = submission_maximum
    adapter._fiber_execution_policy = execution_policy
    adapter._fiber_capture_policy = capture_policy
    adapter._fiber_submission_status = None
    adapter._fiber_intent_routing = intent_routing
    adapter._fiber_media_limit = media_maximum
    adapter._fiber_media_status = None
    adapter._fiber_inbox = (config, binding)


def preflight(adapter: Any) -> tuple[Config, Binding]:
    config, binding = adapter._fiber_inbox
    if adapter._account_id != binding.account_id:
        raise InvalidInput("weixin account binding mismatch")
    inbox.cursor(config, binding)
    return config, binding


async def run(adapter: Any, fetch: Callable[..., Awaitable[object]]) -> None:
    """Called by the candidate Hermes poll loop. Any failure stops without retry.

    Cancellation before capture leaves the old cursor. Once fetch returns, synchronous
    SQLite capture cannot be interrupted by task cancellation midway through commit.
    A process crash is covered by the journal transaction. Restart re-reads its cursor.
    """
    phase = "preflight"
    try:
        config, binding = preflight(adapter)
        phase = "lock"
        with receiver_lock(config):
            quiet = batches.seconds(adapter._fiber_quiet_seconds)
            phase = "batch"
            confirmations.tick(
                config, binding, quiet_seconds=quiet, intent_routing=adapter._fiber_intent_routing
            )
            remaining = outbound.limit(adapter._fiber_outbound_limit)
            store_remaining = store_dispatch.limit(adapter._fiber_store_limit)
            submission_remaining = task_submission.limit(adapter._fiber_submission_limit)
            media_remaining = media_dispatch.limit(adapter._fiber_media_limit)
            media_after = 0
            while adapter._running:
                if media_remaining:
                    phase = "media"
                    recovered = await media_dispatch.drain(
                        config, binding, maximum=1, after=media_after
                    )
                    adapter._fiber_media_status = recovered
                    media_remaining -= recovered["processed"]
                    media_after = recovered["next_cursor"]
                    if recovered["state"] in {"stopped", "blocked", "recovery_required"}:
                        raise InvalidInput("media attempt requires explicit recovery")
                    # Re-evaluate readiness, but never take over an existing confirmation prompt.
                    confirmations.tick(
                        config,
                        binding,
                        quiet_seconds=quiet,
                        intent_routing=adapter._fiber_intent_routing,
                    )
                    await asyncio.sleep(0)
                    if not adapter._running:
                        break
                while store_remaining and adapter._running:
                    phase = "store"
                    completed = store_dispatch.drain(config, binding, maximum=1)
                    if not completed:
                        break
                    store_remaining -= completed
                    await asyncio.sleep(0)  # Cancellation takes effect between batches.
                while submission_remaining and adapter._running:
                    phase = "submission"
                    submitted = task_submission.dispatch(
                        config,
                        binding,
                        adapter._fiber_execution_policy,
                        maximum=1,
                        capture_policy=adapter._fiber_capture_policy,
                    )
                    adapter._fiber_submission_status = submitted
                    submission_remaining -= submitted["processed"]
                    if not submitted["processed"]:
                        break
                    await asyncio.sleep(0)  # Submit only; execution belongs to a separate worker.
                # Only send between polls: a returned poll response is committed first.
                # Never cancel an in-flight/returned fetch because an outbound send failed.
                if remaining:
                    phase = "outbound"
                    remaining -= await outbound.drain(
                        config,
                        binding,
                        adapter._fiber_send_once,
                        maximum=remaining,
                        reminder_sender=getattr(adapter, "_fiber_send_result_once", None),
                        result_sender=adapter._fiber_send_result_once
                        if adapter._fiber_result_receipts
                        else None,
                    )
                if not adapter._running:
                    break
                phase = "preflight"
                preflight(adapter)  # Recheck account and permission before network access.
                expected = inbox.cursor(config, binding)
                phase = "fetch"
                pending = asyncio.ensure_future(
                    fetch(
                        adapter._poll_session,
                        base_url=adapter._base_url,
                        token=adapter._token,
                        sync_buf=expected,
                        timeout_ms=35_000,
                    )
                )
                try:
                    async with asyncio.timeout(45):
                        while not pending.done():
                            await asyncio.wait({pending}, timeout=min(quiet, 1.0))
                            if not pending.done():
                                phase = "batch"
                                confirmations.tick(
                                    config,
                                    binding,
                                    quiet_seconds=quiet,
                                    intent_routing=adapter._fiber_intent_routing,
                                )
                                phase = "fetch"
                        response = pending.result()
                finally:
                    if not pending.done():
                        pending.cancel()
                    # Retrieve cancellation/failure without letting it replace the main error.
                    await asyncio.gather(pending, return_exceptions=True)
                # Do not abandon a returned response just because disconnect was requested.
                # receive rechecks grants after the network await, before committing.
                phase = "capture"
                inbox.receive(config, binding, expected, response)
                phase = "batch"
                confirmations.tick(
                    config,
                    binding,
                    quiet_seconds=quiet,
                    intent_routing=adapter._fiber_intent_routing,
                )
                await asyncio.sleep(0)  # Yield between immediate/empty synthetic responses.
    except asyncio.CancelledError:
        raise
    except Exception:
        # Third-party error text can contain credentials/body. Keep diagnostics categorical.
        adapter._set_fatal_error(
            f"fiber_weixin_{phase}_failed",
            "Weixin capture stopped; inspect local inbox and authorization before restart",
            retryable=False,
        )
    finally:
        adapter._running = False
        # Hermes disconnect otherwise cancels and awaits this very task.
        adapter._poll_task = None
        await adapter.disconnect()
