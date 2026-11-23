"""One bounded background worker for explicit Draft imports, owned by the HTTP lifecycle."""

import threading
from typing import Any

from .. import database, note_search_build
from ..config import StorageError
from . import (
    draft_import_files as files,
)
from . import (
    draft_import_preview as preview,
)
from . import (
    draft_import_tasks as tasks,
)
from . import (
    jobs,
    manual_membership,
    packages,
    store,
)
from .draft_import_copy import Cancelled, Copier


class Runtime:
    def __init__(self, config: Any):
        self.config = config
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        # Recovery never starts a confirmed write without the user's explicit Resume.
        for task in store.rows(self.config, tasks.TABLE):
            if task["state"] in tasks.BUSY:
                with database.locked(self.config):
                    task.update(state="interrupted", error="draft_backend_restarted")
                    tasks.save(self.config, task)
        self.thread = threading.Thread(target=self.loop, name="fiber-draft-import", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self) -> None:
        while not self.stopping.is_set():
            self.wake.wait(1)
            self.wake.clear()
            for task in store.rows(self.config, tasks.TABLE):
                if self.stopping.is_set():
                    return
                if task["state"] == "queued":
                    self.run(task["id"])

    def run(self, identity: str) -> None:
        config = self.config
        with database.locked(config), database.connect(config.state_root) as db:
            task = store.get(config, tasks.TABLE, identity)
            if not task or task["state"] != "queued":
                return
            try:
                tasks.owned(config, db, identity, task["client_id"], task["vault_id"])
            except (StorageError, OSError) as exc:
                task.update(state="failed", error=error_code(exc))
                tasks.save(config, task)
                return
            task["state"] = "working"
            tasks.save(config, task)
        try:
            if not task["confirmed"]:
                observed = preview.prepare(config, task, task["target_evidence"])
                with database.locked(config), database.connect(config.state_root) as db:
                    latest = tasks.owned(config, db, identity, task["client_id"], task["vault_id"])
                    if latest.get("cancel_requested") or self.stopping.is_set():
                        raise Cancelled()
                    task = preview.finish(config, db, observed)
                    tasks.save(config, task)
                return
            Copier(config, task, self.stopping).publish()
            if not (task.get("registered") and task["phase"] == "index"):
                self.register_and_link(task)
            if task["state"] == "partial":
                return
            task["phase"] = "index"
            with database.locked(config):
                tasks.save(config, task)
            if self.stopping.is_set():
                raise Cancelled()
            note_search_build.build(config, task["client_id"], task["vault_id"])
            with database.locked(config), database.connect(config.state_root) as db:
                tasks.owned(config, db, identity, task["client_id"], task["vault_id"])
                task.update(index_state="ready", state="completed", phase="completed", error=None)
                tasks.save(config, task)
                if not task["moc_ids"]:
                    jobs.notify(
                        config,
                        task["vault_id"],
                        "package",
                        task["relative_path"],
                        task["package"]["version"],
                    )
        except Cancelled:
            with database.locked(config):
                task.update(
                    state="interrupted" if self.stopping.is_set() else "cancelled",
                    error="draft_backend_stopping" if self.stopping.is_set() else None,
                )
                tasks.save(config, task)
        except (StorageError, OSError, ValueError, KeyError, TypeError) as exc:
            with database.locked(config):
                task.update(state="failed", error=error_code(exc))
                if task["phase"] == "index":
                    task["index_state"] = "failed"
                tasks.save(config, task)

    def register_and_link(self, task: dict[str, Any]) -> None:
        config = self.config
        # Destination validation is outside the global writer lease.
        root = task["target_evidence"]["root"] + "/" + task["relative_path"]
        observed = files.inspect(
            root, task["vault_id"], task["relative_path"], task["target_evidence"]["root_key"]
        )
        if observed["package"]["version"] != task["package"]["version"]:
            raise StorageError("draft_destination_changed")
        with database.locked(config), database.connect(config.state_root) as db:
            latest = tasks.owned(config, db, task["id"], task["client_id"], task["vault_id"])
            if latest.get("cancel_requested") or self.stopping.is_set():
                raise Cancelled()
            preview.recheck_target(config, db, task)
            files.recheck(observed)
            packages.commit(
                config, db, task["client_id"], task["vault_id"], observed["package"], notify=False
            )
            task["registered"] = True
            task["phase"] = "membership"
            tasks.save(config, task)
            successful = {r["moc_id"] for r in task["moc_results"] if r["completed"]}
            remaining = [m for m in task["moc_ids"] if m not in successful]
            if remaining:
                result = manual_membership.apply(
                    config,
                    db,
                    task["client_id"],
                    task["vault_id"],
                    task["package"]["module_id"],
                    remaining,
                    "link",
                )
                task["moc_results"] = [r for r in task["moc_results"] if r["moc_id"] in successful]
                task["moc_results"].extend(result["results"])
                if not result["completed"]:
                    task.update(state="partial", error="draft_membership_incomplete")
            tasks.save(config, task)


def error_code(exc: Exception) -> str:
    value = str(exc)
    if value and len(value) <= 100 and all(c.isalnum() or c == "_" for c in value):
        return value
    return "draft_io_or_validation_failed"
