"""Task-bound repair of absent Map projections; human blocks remain protected."""

import re
from typing import Any

from .. import moc_file_journal, moc_markdown
from ..config import IntegrityError
from ..objects import digest
from . import manual_resolution, packages


def manual_change(config: Any, db: Any, job: Any, mid: str, current: Any, expected: Any) -> bool:
    """Accept only exact, audited manual projections for resolved task items."""
    records = manual_resolution.records(config, job)
    if not records or not any(
        row.get("moc_evidence", {}).get(mid) == current for row in records.values()
    ):
        return False
    for entity in set(current["members"]) | set(expected["members"]):
        before = set(expected["members"].get(entity, []))
        after = set(current["members"].get(entity, []))
        if before != after and (entity not in records or after != before | {"manual"}):
            return False
    moc = next(m for m in job["mocs"] if m["id"] == mid)
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    raw = packages.scoped_read(root, allowed, moc["path"])

    def topic(text: str) -> str:
        return re.sub(r"<!-- fiber-moc:.*?end -->", "", text, flags=re.S).rstrip("\r\n")

    return digest(raw) == current["sha256"] and topic(raw.decode("utf-8-sig")) == topic(moc["text"])


def planning_mocs(config: Any, db: Any, job: Any, mocs: Any) -> Any:
    """Keep frozen topic fingerprints across evidenced manual projection appends."""
    if not manual_resolution.resolved(config, job):
        return mocs
    from .maintenance_apply import observe

    bound = {m["id"]: m for m in job["mocs"]}
    result = []
    for moc in mocs:
        prior = bound.get(moc["id"])
        if prior and (moc["sha256"] != prior["sha256"] or moc["text"] != prior["text"]):
            expected = {k: prior[k] for k in ("sha256", "revision", "members")}
            if manual_change(
                config, db, job, moc["id"], observe(config, db, job, moc["id"]), expected
            ):
                moc = {**moc, "text": prior["text"]}
        result.append(moc)
    return result


def preflight(config: Any, db: Any, job: Any) -> None:
    """Read all affected Maps before changing any relationship.

    A missing block may be appended only if it was already absent in this
    task's bound snapshot. Keep the immutable chain and bind its exact head.
    Edited or malformed blocks are never adopted by this path.
    """
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    handled = manual_resolution.resolved(config, job)
    active = [i for i in job["items"] if i.get("entity") not in handled]
    touched = {mid for i in active for mid in i["add"] + i["remove"]}
    for moc in job["mocs"]:
        mid = moc["id"]
        if mid not in touched or moc.get("new"):
            continue
        try:
            raw = packages.scoped_read(root, allowed, moc["path"])
            _, block, _ = moc_markdown.split(raw, mid)
            plans = moc_file_journal.plans(config, job["vault_id"], mid)
            pending = [p for p in plans if p.status == "pending"]
            if pending:
                # The projection writer reconciles its exact inode exchange;
                # do not mistake an interrupted task-owned exchange for an edit.
                if any(i.get("intent") == "link:" + mid for i in active):
                    continue
                raise IntegrityError("maintenance_moc_pending_write")
            prior = moc_file_journal.latest(plans)
            if prior is None or digest(block) == prior.value["managed_sha256"]:
                continue
            if block:
                raise IntegrityError("maintenance_moc_managed_block_edited")
            expected = job.get("moc_expected", {}).get(mid, moc)
            if digest(raw) != expected["sha256"]:
                raise IntegrityError("maintenance_moc_changed")
            repair = {"path": moc["path"], "sha256": digest(raw), "base_id": prior.plan_id}
            saved = job.setdefault("moc_repairs", {}).setdefault(mid, repair)
            if saved != repair:
                raise IntegrityError("maintenance_moc_changed")
        except IntegrityError:
            job["error_detail"] = {"moc": moc["title"], "path": moc["path"]}
            raise


def repair_base(job: Any, mid: str, current_sha256: str) -> tuple[str, str] | None:
    repair = job.get("moc_repairs", {}).get(mid)
    return (
        (repair["base_id"], repair["sha256"])
        if repair and repair["sha256"] == current_sha256
        else None
    )


def own_link(current: Any, expected: Any, entity: str) -> bool:
    wanted = {key: list(owners) for key, owners in expected["members"].items()}
    wanted[entity] = sorted(set(wanted.get(entity, [])) | {"system"})
    return bool(current["revision"] == expected["revision"] + 1 and current["members"] == wanted)
