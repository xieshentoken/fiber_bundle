"""One finite migration stage per tick; browsing status never advances work."""

import logging
import threading

from .. import database
from ..config import Config
from . import execution


class Runtime:
    def __init__(self, config: Config):
        self.config = config
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        execution.startup(self.config)
        self.thread = threading.Thread(target=self._loop, name="vault-relocation", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self) -> None:
        while not self.stopped.wait(0.25):
            try:
                with database.connect(self.config.state_root, readonly=True) as db:
                    ids = [
                        r["id"]
                        for r in db.execute(
                            "SELECT id,state FROM vault_operations WHERE kind='vault_relocation'"
                        )
                        if r["state"] in execution.ACTIVE
                    ]
                for identity in ids:
                    if self.stopped.is_set():
                        return
                    execution.step(self.config, identity)
            except Exception:
                logging.getLogger(__name__).exception("Vault relocation stage failed")
