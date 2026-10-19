"""Independent bounded Weixin execution and planned publication, separate from intake."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event
from typing import Any

from . import weixin_inbox as inbox
from . import weixin_publication_dispatch as publishing
from . import weixin_task_dispatch as dispatch
from .config import Config, InvalidInput, contained
from .execution_config import ExecutionConfig
from .weixin_worker_config import WorkerConfig


@contextmanager
def session_lock(config: Config) -> Iterator[None]:
    """Hold across polling waits; per-dispatch and native run locks remain independent."""
    config.validate()
    fd = os.open(
        contained(config.state_root, "weixin-execution-worker.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("Weixin execution worker already active") from None
        yield
    finally:
        os.close(fd)


def run(
    config: Config,
    settings: WorkerConfig,
    policy: ExecutionConfig | None = None,
    *,
    stop: Event | None = None,
) -> dict[str, Any]:
    """Fixed policy and a total attempt/poll budget; never enqueue, recover or retry.

    A stop event prevents the next claim and interrupts idle waits. In-flight execution
    retains its native timeout/cancellation checks. Process signals may interrupt it;
    persistent claims and fees then require explicit operator reconciliation.
    """
    settings.validate(config)
    result: dict[str, Any] = {"state": "disabled", "processed": 0, "polls": 0, "results": []}
    if not settings.enabled or not (settings.maximum or settings.publication_maximum):
        return result
    if settings.maximum:
        if not isinstance(policy, ExecutionConfig):
            raise InvalidInput("enabled worker requires an explicit execution policy")
        policy.validate()
    if settings.publication_maximum:
        result.update(publication_processed=0, publications=[])
    inbox.cursor(config, settings.binding)  # No automatic creation or rebind.
    signal = stop if stop is not None else Event()
    with session_lock(config):
        for number in range(settings.poll_limit):
            if signal.is_set():
                result["state"] = "interrupted"
                return result
            completed: dict[str, Any] = {"state": "idle", "processed": 0, "results": []}
            if result["processed"] < settings.maximum:
                assert isinstance(policy, ExecutionConfig)
                completed = dispatch.dispatch(config, settings.binding, policy, maximum=1)
            result["polls"] += 1
            result["processed"] += completed["processed"]
            result["results"].extend(completed["results"])
            if completed["state"] in {"blocked", "stopped"}:
                result["state"] = completed["state"]
                if "blocked" in completed:
                    result["blocked"] = completed["blocked"]
                return result
            publication_count = result.get("publication_processed", 0)
            if publication_count < settings.publication_maximum:
                published = publishing.dispatch(config, settings.binding, maximum=1)
                result["publication_processed"] += published["processed"]
                result["publications"].extend(published["results"])
                if published["state"] in {"blocked", "stopped"}:
                    result["state"] = published["state"]
                    result["blocked_run_id"] = published["blocked_run_id"]
                    return result
                publication_count = result["publication_processed"]
                if published["processed"]:
                    completed["state"] = "published"
            if (
                result["processed"] == settings.maximum
                and publication_count == settings.publication_maximum
            ):
                result["state"] = "limit_reached"
                return result
            if number + 1 < settings.poll_limit:
                # Wait only when idle; a ready queue drains without artificial execution delays.
                if completed["state"] == "idle":
                    signal.wait(settings.poll_seconds)
        result["state"] = "poll_limit_reached"
    return result
