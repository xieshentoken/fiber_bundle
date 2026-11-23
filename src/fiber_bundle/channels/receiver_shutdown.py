"""Bound receiver cleanup without releasing ownership above unfinished worker threads."""

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .. import feishu_runtime


@contextmanager
def deadline(home: Path, seconds: float = 120) -> Iterator[None]:
    """Allow admitted work to join; terminate the whole receiver if cleanup cannot finish.

    Cancelling an asyncio wait does not stop a synchronous writer. The process must
    retain its account/instance leases until all workers finish or the OS ends all
    its threads. Existing send/model claims remain unresolved and are never reset.
    """
    finished = False
    gate = threading.Lock()

    def expire() -> None:
        with gate:
            if finished:
                return
            try:
                feishu_runtime.write(
                    home / "shutdown-status.json",
                    {
                        "state": "needs_review",
                        "error": "channel_shutdown_timeout",
                        "observed_at": time.time(),
                        "pid": os.getpid(),
                    },
                )
            finally:
                os._exit(1)

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        with gate:
            finished = True
            timer.cancel()
