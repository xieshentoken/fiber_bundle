"""One durable work unit; release the writer only at audited read-only boundaries.

All writes retain the global lease and existing full file/CAS checks. A concurrent
metadata/configuration change invalidates an unlocked read before it can be used.
The separate process lease prevents a second worker from executing the same unit.
"""

from typing import Any

from .. import database, storage_read_scope
from ..config import IntegrityError
from ..storage_versions import stamp
from . import execution_control, packages, store


class Unit:
    def __init__(self, config: Any, db: Any, job: Any) -> None:
        self.config, self.db, self.job = config, db, job
        self.lock = database.locked(config)
        self.reading = False

    def binding(self) -> Any:
        root, scope = packages.context(
            self.config, self.db, self.job["client_id"], self.job["vault_id"], write=True
        )
        info = root.stat()
        return str(root), info.st_dev, info.st_ino, scope.wire()

    def read(self, operation: Any) -> Any:
        if self.reading or self.db.in_transaction:
            return operation()
        # Publish the current phase before its slow read; no source text is added.
        published = store.get(self.config, "jobs", self.job["id"])
        if self.job.get("activity"):
            published["activity"] = dict(self.job["activity"])
            store.put(self.config, "jobs", self.job["id"], self.job["vault_id"], published)
        binding = self.binding()
        before = stamp(self.config, self.job["vault_id"])
        self.reading = True
        self.lock.__exit__(None, None, None)
        try:
            value = operation()
        finally:
            self.lock = database.locked(self.config)
            self.lock.__enter__()
            self.reading = False
        if before != stamp(self.config, self.job["vault_id"]):
            raise IntegrityError("organization_execution_inputs_changed")
        if binding != self.binding():
            raise IntegrityError("organization_execution_inputs_changed")
        return value


def run(config: Any, identity: str) -> None:
    from . import maintenance

    with execution_control.claim(config, identity) as acquired:
        if not acquired:
            return
        with database.connect(config.state_root) as db:
            unit = Unit(config, db, None)
            unit.lock.__enter__()
            token = storage_read_scope.current.set(unit.read)
            try:
                job = unit.job = store.get(config, "jobs", identity)
                if not job or job["state"] not in {"queued", "running", "cancelling"}:
                    return
                from ..vault_relocation.guard import operation

                if operation(db, job["vault_id"]):
                    return
                execution_control.settle(config, job)
                if job["state"] != "paused":
                    maintenance.run(config, db, job)
                execution_control.settle(config, job)
                store.put(config, "jobs", identity, job["vault_id"], job)
            except Exception as error:
                if unit.job:
                    unit.job.update(
                        state="partial", error=type(error).__name__ + ": " + str(error)[:120]
                    )
                    execution_control.settle(config, unit.job)
                    store.put(config, "jobs", identity, unit.job["vault_id"], unit.job)
            finally:
                storage_read_scope.current.reset(token)
                unit.lock.__exit__(None, None, None)
