"""Fresh membership evidence for frozen plans and response-only task progress."""

from typing import Any

from .. import auth, moc_markdown, moc_registration, moc_store, note_identity_store
from ..config import IntegrityError, StorageError
from ..objects import digest
from . import manual_resolution, note_moc_sync, packages, store
from .manual_relations import ManualRelations


class Observation:
    """Bounded reads of task-referenced Maps/notes, cached only for this response."""

    def __init__(self, config: Any, db: Any, jobs: Any):
        job = jobs[0]
        self.config, self.db = config, db
        self.client, self.vault = job["client_id"], job["vault_id"]
        self.root, self.allowed = packages.context(config, db, self.client, self.vault)
        self.mocs = {m["id"]: m for j in jobs for m in j.get("mocs", [])}
        self.paths = {
            mid: path for path, mid in moc_registration.bindings(config, self.vault).items()
        }
        for mid, path in self.paths.items():
            if self.allowed.allows(path):
                self.mocs.setdefault(mid, {"id": mid, "path": path})
        self.current: dict[str, Any] = {}
        self.failures: dict[str, Exception] = {}
        self.entities: dict[str, Any] = {}
        self.registrations: Any = None
        self.manual: Any = None
        self.excluded = {
            (d["entity"], d["moc"])
            for d in store.rows(config, "decisions", self.vault)
            if d["state"] in {"rejected", "retired"}
        }

    def moc(self, mid: str) -> Any:
        if mid in self.failures:
            raise self.failures[mid]
        if mid not in self.current:
            moc = self.mocs.get(mid)
            if moc is None:
                raise IntegrityError("maintenance_membership_plan_changed")
            path = self.paths.get(mid, moc["path"])
            try:
                raw = packages.scoped_read(self.root, self.allowed, path)
                moc_markdown.split(raw, mid)
                record = moc_store.load(self.config, self.vault, mid)
                self.current[mid] = {
                    "id": mid,
                    "path": path,
                    "text": raw.decode("utf-8-sig"),
                    "sha256": digest(raw),
                    "revision": record.revision if record else 0,
                    "members": record.members if record else {},
                }
            except (StorageError, OSError, UnicodeError, auth.Denied) as error:
                self.failures[mid] = error
                raise
        return self.current[mid]

    def entity(self, item: Any) -> Any:
        identity = item["entity"]
        if identity not in self.entities:
            path = item["path"]
            if item["kind"] == "note":
                if self.registrations is None:
                    self.registrations = {
                        r.identity: r for r in note_identity_store.records(self.config, self.vault)
                    }
                registration = self.registrations.get(identity)
                if registration:
                    path = registration.relative_path
                elif item.get("phase") == "archived" and item.get("target"):
                    path = item["target"]
                try:
                    raw = packages.scoped_read(self.root, self.allowed, path)
                except FileNotFoundError:
                    from .maintenance_archive import moved_source

                    moved = moved_source(self.config, self.db, self.client, self.vault, item)
                    if moved is None:
                        raise
                    path, raw = moved
                entity = {
                    "id": identity,
                    "kind": "note",
                    "path": path,
                    "text": raw.decode("utf-8-sig"),
                    "sha256": digest(raw),
                }
                note_moc_sync.decorate(
                    entity,
                    store.get(self.config, "note_moc", note_moc_sync.key(self.vault, identity)),
                )
            else:
                from . import text_features

                module = packages.load(
                    self.config,
                    self.db,
                    self.client,
                    identity.removeprefix("expert:"),
                    item["sha256"],
                    text_only=True,
                )
                entity = {
                    "id": identity,
                    "kind": "expert",
                    "path": path,
                    "text": text_features.expert(module)["text"],
                }
            self.entities[identity] = entity
        return self.entities[identity]

    def contains(self, item: Any, mid: str) -> bool:
        if (item["entity"], mid) in self.excluded:
            return False
        moc = self.moc(mid)
        if item["entity"] in moc["members"]:
            return True
        # Resolve human frontmatter/body/backlinks against current Map paths.
        # An unreadable unrelated Map contributes no backlink, but keeps its name
        # in the ambiguity check. Fiber-owned note fields never count as human links.
        if self.manual is None:
            mocs = []
            for identity, bound in self.mocs.items():
                try:
                    current = self.moc(identity)
                except (StorageError, OSError, UnicodeError, auth.Denied):
                    current = {
                        "id": identity,
                        "path": self.paths.get(identity, bound["path"]),
                        "text": "",
                        "members": {},
                    }
                mocs.append(current)
            self.manual = ManualRelations(mocs)
        return mid in self.manual.memberships(self.entity(item))

    def project(self, item: Any) -> Any:
        receipt = item.get("manual_resolution", {})
        wanted = sorted(set(receipt.get("moc_ids") or item.get("after", [])))
        if not wanted:
            return {"state": "unplanned", "moc_ids": []}
        present: list[str] = []
        missing: list[str] = []
        unavailable: list[dict[str, str]] = []
        for mid in wanted:
            try:
                (present if self.contains(item, mid) else missing).append(mid)
            except (StorageError, OSError, UnicodeError, auth.Denied) as error:
                unavailable.append({"moc_id": mid, "error": str(error)[:180]})
        return {
            "state": "unavailable" if unavailable else "missing" if missing else "current",
            "moc_ids": present,
            "missing_moc_ids": missing,
            "unavailable": unavailable,
        }


def validate_item(
    config: Any, db: Any, job: Any, item: Any, *, complete: bool = False, observation: Any = None
) -> None:
    """Check retained relations before writes, then the whole after-set before archive.

    Planned additions retain their existing intent/journal recovery path. This
    function never recalculates a plan or adopts an external MOC revision.
    """
    required = set(item["after"])
    if not complete and item.get("phase") not in {"members_done", "archived"}:
        required -= {mid for mid in item["add"] if "link:" + mid not in item.get("steps", [])}
    if not required:
        return
    observed = observation or Observation(config, db, [job])
    for mid in sorted(required):
        moc: Any = next((m for m in job["mocs"] if m["id"] == mid), {})
        detail = {
            "entity": item["entity"],
            "path": item["path"],
            "moc": moc.get("title", mid),
            "moc_path": moc.get("path"),
        }
        if not observed.contains(item, mid):
            job["error_detail"] = detail
            raise IntegrityError("maintenance_membership_plan_changed")
        current = {k: observed.moc(mid)[k] for k in ("sha256", "revision", "members")}
        expected = job.get("moc_expected", {}).get(mid)
        if current == expected:
            continue
        from . import maintenance_archive, maintenance_moc_recovery

        if expected and (
            maintenance_archive.own_projection(db, job, item, mid, current, expected)
            or maintenance_archive.own_update(config, db, job, item, mid, current, expected)
            or maintenance_moc_recovery.manual_change(config, db, job, mid, current, expected)
        ):
            continue
        job["error_detail"] = detail
        raise IntegrityError("maintenance_membership_baseline_changed")


def validate_resume(config: Any, db: Any, job: Any) -> None:
    handled = manual_resolution.resolved(config, job)
    observation = Observation(config, db, [job])
    for item in job["items"]:
        if item["state"] != "done" and item["entity"] not in handled:
            validate_item(config, db, job, item, observation=observation)
