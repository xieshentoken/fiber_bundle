"""Resumable stream-copy and no-replace publication of one inspected external Draft."""

import os
import time
from typing import Any
from uuid import uuid4

from .. import database, file_exchange, publication_file_io
from ..config import IntegrityError, contained
from ..objects import digest
from . import draft_import_files as files
from . import draft_import_preview as preview
from . import draft_import_tasks as tasks
from . import packages


class Cancelled(Exception):
    pass


class Copier:
    def __init__(self, config: Any, task: dict[str, Any], stopping: Any):
        self.config, self.task, self.stopping = config, task, stopping
        self.last = 0.0
        self.done = 0

    def progress(self, count: int = 0, *, force: bool = False) -> None:
        self.done += count
        if not force and time.monotonic() - self.last < 0.3:
            return
        if self.stopping.is_set():
            raise Cancelled()
        with database.locked(self.config), database.connect(self.config.state_root) as db:
            latest = tasks.owned(
                self.config, db, self.task["id"], self.task["client_id"], self.task["vault_id"]
            )
            if latest.get("cancel_requested"):
                raise Cancelled()
            _, allowed = packages.context(
                self.config, db, self.task["client_id"], self.task["vault_id"], write=True
            )
            if allowed.wire() != self.task["target_evidence"]["scope"]:
                raise IntegrityError("draft_target_changed")
            self.task["copied_bytes"] = self.done
            tasks.save(self.config, self.task)
        self.last = time.monotonic()

    def publish(self) -> None:
        t, config = self.task, self.config
        with database.locked(config), database.connect(config.state_root) as db:
            root, _ = preview.recheck_target(config, db, t)
            destination = contained(root, t["relative_path"])
            if t.get("published_key"):
                if files.key(destination) != t["published_key"]:
                    raise IntegrityError("draft_destination_changed")
                return
            # A crash after atomic rename is adopted only with the recorded payload identity.
            if t.get("payload_key") and destination.exists():
                if files.key(destination) != t["payload_key"]:
                    raise IntegrityError("draft_destination_changed")
                t["published_key"] = t["payload_key"]
                tasks.save(config, t)
                return
        source = files.recheck(t)
        if t["reuse"]:
            with database.locked(config), database.connect(config.state_root) as db:
                preview.recheck_target(config, db, t)
                t["published_key"] = files.key(destination)
                tasks.save(config, t)
            return
        stage_relative = t["target_evidence"]["book_dir"] + "/.fiber-draft-" + t["id"]
        stage = contained(root, stage_relative)
        with database.locked(config), database.connect(config.state_root) as db:
            preview.recheck_target(config, db, t)
            if t.get("stage_key"):
                if files.key(stage) != t["stage_key"]:
                    raise IntegrityError("draft_staging_changed")
            else:
                parent = file_exchange.parent_fd(root, stage_relative)
                try:
                    os.mkdir(stage.name, 0o700, dir_fd=parent)
                    os.fsync(parent)
                finally:
                    os.close(parent)
                t["stage_key"] = files.key(stage)
                (stage / "payload").mkdir(mode=0o700)
                t["payload_key"] = files.key(stage / "payload")
                fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                tasks.save(config, t)
        payload = stage / "payload"
        self.done = 0
        t["copied_files"] = 0
        for name, expected in t["package"]["files"].items():
            self.progress()
            if files.key(stage) != t["stage_key"] or files.key(payload) != t["payload_key"]:
                raise IntegrityError("draft_staging_changed")
            target = contained(payload, name)
            if target.exists():
                raw = publication_file_io.read(payload, name)[0]
                if digest(raw) != expected["sha256"]:
                    raise IntegrityError("draft_staging_changed")
                self.done += len(raw)
            else:
                # Interrupted partials stay outside payload. They cannot be indexed or published.
                part = stage / ("partial-" + str(uuid4()))
                files.copy_member(
                    source, name, part, expected, t["source_files"][name], self.progress
                )
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                target_fd = file_exchange.parent_fd(payload, name)
                try:
                    file_exchange.rename(
                        source_fd, part.name, target_fd, target.name, exchange=False
                    )
                finally:
                    os.close(source_fd)
                    os.close(target_fd)
            t["copied_files"] += 1
        self.progress(force=True)
        t["phase"] = "verification"
        with database.locked(config):
            tasks.save(config, t)
        checked = files.inspect(
            str(payload), t["vault_id"], t["relative_path"], t["target_evidence"]["root_key"]
        )
        if checked["package"]["version"] != t["package"]["version"]:
            raise IntegrityError("draft_staging_changed")
        files.recheck(t)
        self.progress(force=True)
        with database.locked(config), database.connect(config.state_root) as db:
            preview.recheck_target(config, db, t)
            files.recheck(checked)
            if files.key(stage) != t["stage_key"] or files.key(payload) != t["payload_key"]:
                raise IntegrityError("draft_staging_changed")
            source_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            target_fd = file_exchange.parent_fd(root, t["relative_path"])
            try:
                file_exchange.rename(
                    source_fd, "payload", target_fd, destination.name, exchange=False
                )
            finally:
                os.close(source_fd)
                os.close(target_fd)
            t["published_key"] = t["payload_key"]
            t["phase"] = "registration"
            tasks.save(config, t)
