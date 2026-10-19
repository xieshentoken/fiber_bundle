"""Short authorization leases around source reads performed outside the writer lock."""

from typing import Any

from .. import database
from ..config import IntegrityError, InvalidInput
from ..objects import digest
from . import maintenance, maintenance_moc_recovery, manual_resolution, packages, store


class AnalysisGuard:
    def __init__(self, config: Any, job: Any, entity: Any, mocs: Any):
        self.config, self.job, self.entity, self.mocs = config, job, entity, mocs

    def authorized(self, db: Any) -> Any:
        current = store.get(self.config, "jobs", self.job["id"])
        if (
            not current
            or current["state"] not in {"queued", "running"}
            or current["stage"] != "planning"
        ):
            raise InvalidInput("maintenance_cancelled")
        maintenance.validate(self.config, db, current, sources=False)
        if current["fixed"] != self.job["fixed"]:
            raise IntegrityError("maintenance_authorization_or_policy_changed")
        if current.get("generation", 0) != self.job.get("generation", 0):
            raise InvalidInput("maintenance_cancelled")
        handled = manual_resolution.resolved(self.config, current)
        if self.entity is not None and self.entity["id"] in handled:
            raise InvalidInput("maintenance_cancelled")
        expected = {
            i["entity"]: i.get("decisions", [])
            for i in self.job["items"]
            if i["entity"] not in handled
        }
        decisions: dict[str, list[Any]] = {}
        for row in store.rows(self.config, "decisions", self.job["vault_id"]):
            decisions.setdefault(row["entity"], []).append(row)
        if any(decisions.get(entity, []) != values for entity, values in expected.items()):
            raise IntegrityError("maintenance_manual_decision_changed")
        return current

    def __call__(self) -> None:
        with database.locked(self.config), database.connect(self.config.state_root) as db:
            self.authorized(db)
            root, allowed = packages.context(
                self.config, db, self.job["client_id"], self.job["vault_id"], write=True
            )
        # These files can be large. Recheck authorization after reading, before any send/commit.
        for moc in self.mocs:
            if moc.get("new"):
                if (root / moc["path"]).exists():
                    raise IntegrityError("maintenance_moc_changed")
            elif digest(packages.scoped_read(root, allowed, moc["path"])) != moc["sha256"]:
                from .maintenance_apply import observe

                with database.connect(self.config.state_root, readonly=True) as db:
                    expected = {
                        "sha256": moc["sha256"],
                        "revision": moc["revision"],
                        "members": moc["members"],
                    }
                    if not maintenance_moc_recovery.manual_change(
                        self.config,
                        db,
                        self.job,
                        moc["id"],
                        observe(self.config, db, self.job, moc["id"]),
                        expected,
                    ):
                        raise IntegrityError("maintenance_moc_changed")
        if self.entity is not None:
            if self.entity["kind"] == "note":
                if (
                    digest(packages.scoped_read(root, allowed, self.entity["path"]))
                    != self.entity["sha256"]
                ):
                    raise IntegrityError("maintenance_note_changed")
            else:
                with database.connect(self.config.state_root, readonly=True) as db:
                    packages.load(
                        self.config,
                        db,
                        self.job["client_id"],
                        self.entity["id"].removeprefix("expert:"),
                        self.entity["sha256"],
                        text_only=True,
                    )
        with database.locked(self.config), database.connect(self.config.state_root) as db:
            self.authorized(db)
