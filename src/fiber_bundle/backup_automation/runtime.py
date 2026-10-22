"""One backend-owned thread; disabled/unconfigured installs perform no snapshot IO."""

import logging
import re
import threading
import time
from typing import Any

from ..config import Config, StorageError
from . import runner, scheduler, store


class Runtime:
    def __init__(self, config: Config):
        self.config = config
        self.observation_cache: dict[str, Any] = {}
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.recovered = False

    def start(self) -> None:
        try:
            runner.recover(self.config)
            self.recovered = True
        except (StorageError, OSError, ValueError):
            logging.getLogger(__name__).error("Backup recovery blocked; ledger retained")
        self.thread = threading.Thread(target=self.loop, name="backup-queue", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self) -> None:
        while not self.stop.wait(2):
            try:
                if not self.recovered:
                    runner.recover(self.config)
                    self.recovered = True
                runner.execute(self.config)
                scheduler.tick(self.config, observation_cache=self.observation_cache)
            except Exception as exc:
                code = str(exc)
                code = (
                    code
                    if re.fullmatch(r"backup_[a-z_]{1,80}", code)
                    else "backup_observation_unavailable"
                )
                try:
                    with store.edit(self.config) as value:
                        value.update(error=code, checked_at=time.time())
                except Exception:
                    logging.getLogger(__name__).error("Backup queue needs attention")
