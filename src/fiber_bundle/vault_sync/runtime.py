"""One backend coordinator owns all observers and serial metadata work."""

import hashlib
import logging
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .. import database, vault_catalog, vault_scope
from ..config import Config, StorageError
from ..inputs import canonical
from . import events, index, maintenance, scanner, store


class Runtime:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.streams: dict[str, events.Stream] = {}
        self.bindings: dict[str, str] = {}
        self.retry: dict[str, float] = {}
        self.executable: Path | None = None
        self.last_catalog = self.last_heartbeat = 0.0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vault-index")
        self.index_work: tuple[str, Future[dict[str, object]]] | None = None

    def start(self) -> None:
        # Native packaged backend opts into events; CLI/test installs without the helper
        # keep their explicit scan semantics instead of silently starting polling.
        try:
            self.executable = events.helper()
        except (StorageError, OSError):
            logging.getLogger(__name__).error("Vault event helper unavailable")
            return
        if self.executable is None:
            return
        self.thread = threading.Thread(target=self.loop, name="vault-sync", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def catalog(self, now: float) -> None:
        with database.connect(self.config.state_root, readonly=True) as db:
            rows = db.execute(
                "SELECT * FROM vaults WHERE status='active' AND attachment_state='attached'"
            ).fetchall()
        live = {row["id"] for row in rows}
        for identity in set(self.streams) - live:
            self.streams.pop(identity).close()
            self.bindings.pop(identity, None)
        for row in rows:
            identity = row["id"]
            binding = canonical(
                [
                    row[k]
                    for k in (
                        "absolute_root",
                        "relative_path",
                        "directory_dev",
                        "directory_ino",
                        "scope_json",
                    )
                ]
            )
            prior = store.library(self.config, identity)
            unchanged = (
                self.bindings.get(identity) == binding
                and prior is not None
                and self.streams[identity].scope == prior["scope"]
            )
            if unchanged or self.retry.get(identity, 0) > now:
                continue
            try:
                root = vault_catalog.physical_path(self.config, row)
                scope = vault_scope.for_vault(root, row)
                info = root.lstat()
                if row["directory_dev"] is not None and (info.st_dev, info.st_ino) != (
                    row["directory_dev"],
                    row["directory_ino"],
                ):
                    raise StorageError("vault_sync_root_changed")
                if self.bindings.get(identity) != binding:
                    store.register(
                        self.config,
                        identity,
                        str(root),
                        scope,
                        hashlib.sha256(binding.encode()).hexdigest(),
                    )
                if identity in self.streams:
                    self.streams.pop(identity).close()
                observed = store.library(self.config, identity)
                assert observed is not None and self.executable is not None
                self.streams[identity] = events.Stream(self.config, observed, self.executable)
                self.bindings[identity] = binding
            except (StorageError, OSError, ValueError):
                self.retry[identity] = now + 60
                logging.getLogger(__name__).warning("Vault event observer waiting for availability")

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if now - self.last_catalog >= 15 and self.index_work is None:
            self.catalog(now)  # Local catalogue only; unchanged bindings never touch Vault paths.
            self.last_catalog = now
        for identity, stream in list(self.streams.items()):
            try:
                stream.drain()
            except (StorageError, OSError, ValueError, KeyError):
                stream.close()
                del self.streams[identity]
                self.bindings.pop(identity, None)
                self.retry[identity] = now + 60
        if self.index_work is not None:
            identity, future = self.index_work
            if future.done():
                error = future.exception()
                if error is not None:
                    logging.getLogger(__name__).warning(
                        "Vault index maintenance waiting",
                        exc_info=(type(error), error, error.__traceback__),
                    )
                maintenance.finished(
                    self.config, identity, failed=error is not None, now=now, error=error
                )
                self.index_work = None
        if now - self.last_heartbeat >= 15:
            for identity in self.streams:
                scanner.heartbeat(self.config, identity, now=now)
                value = store.status(self.config, identity)
                if (
                    value
                    and value["baseline"]
                    and not value["pending"]
                    and (now - value["last_audit"] >= store.AUDIT_INTERVAL)
                ):
                    row = store.library(self.config, identity)
                    assert row is not None
                    store.enqueue(self.config, identity, [("", 1)], row["cursor"], now=now)
                    with store.connection(self.config, write=True) as db:
                        assert db is not None
                        db.execute("UPDATE libraries SET last_audit=? WHERE id=?", (now, identity))
            self.last_heartbeat = now
        # One bounded unit at a time across every Vault/volume. No storage writer lock is
        # held while enumerating iCloud. Event callbacks never launch scans themselves.
        if self.index_work is not None:
            return
        tasks = store.due(self.config, now=now, limit=1)
        for task in tasks:
            if task["vault"] not in self.streams:
                continue
            try:
                scanner.apply(self.config, task, now=now)
            except (StorageError, OSError, ValueError):
                store.failed(self.config, task, "vault_sync_source_unavailable", now=now)
        if not tasks and (job := maintenance.next_task(self.config, set(self.streams), now)):
            identity, client = job
            self.index_work = (
                identity,
                self.executor.submit(index.update, self.config, client, identity, self.stop),
            )

    def loop(self) -> None:
        try:
            while not self.stop.is_set():
                try:
                    self.tick()
                except (StorageError, OSError, ValueError, sqlite3.Error):
                    logging.getLogger(__name__).exception("Vault sync coordinator needs attention")
                    self.stop.wait(30)
                self.stop.wait(0.5)
        finally:
            for stream in self.streams.values():
                stream.close()
