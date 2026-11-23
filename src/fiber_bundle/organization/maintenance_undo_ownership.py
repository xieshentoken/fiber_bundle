"""Bound compensation to recorded task files, entities and operation evidence."""

import json
from typing import Any

from ..config import IntegrityError
from ..inputs import canonical
from . import maintenance_undo_blobs as blobs
from . import maintenance_undo_state as state


def operations(db: Any, job: Any, journal: Any) -> set[str]:
    result = {item["archive_operation"] for item in job["items"] if item.get("archive_operation")}
    result.update(i["retire_adoption"] for i in job["items"] if i.get("retire_adoption"))
    result.update(m["registration"] for m in job["new_mocs"] if m.get("registration"))
    # Ordinary existing Maps are adopted inside membership.apply. Their durable
    # file plan, rather than creation time, proves which registration is ours.
    plans = {p["id"] for p in journal["plans"]}
    for row in db.execute(
        "SELECT id,json_extract(payload,'$.plan_id'),state,json_extract(payload,'$.path') "
        "FROM vault_operations "
        "WHERE vault_id=? AND kind='moc_registration'",
        (job["vault_id"],),
    ):
        if row[1] in plans:
            result.add(row[0])
        elif (
            row[0] not in result
            and row[2] in {"applying", "reconcile"}
            and row[3] in journal["files"]
        ):
            # Legacy membership adoption may crash before saving its plan ID.
            # Keep the ambiguous operation intact and do not claim cancellation.
            raise IntegrityError("maintenance_undo_relationship_conflict")
    return result


def seal(config: Any, job: Any, journal: Any, current: Any) -> None:
    """Keep unowned rows on both sides, including journals created before this guard.

    A delayed seal can run after other writers have committed new notes. A
    whole-vault difference is not evidence that this task authored those writes.
    """
    paths = set(journal["files"])
    packages: list[str] = []
    for move in journal["moves"]:
        paths.update((move["source"], move["target"]))
        if move.get("package"):
            packages.extend((move["source"] + "/", move["target"] + "/"))

    def touched(path: Any) -> bool:
        return isinstance(path, str) and (
            path in paths or any(path.startswith(prefix) for prefix in packages)
        )

    entities = {i["entity"] for i in job["items"] if touched(i["path"])}
    mocs = {m["id"] for m in job["mocs"] if touched(m["path"])}
    before, after = journal["before_state"], journal["after_state"]

    def owned(section: str, key: str, rows: list[Any]) -> bool:
        if section == "mocs":
            return key in mocs
        if section == "identities":
            return key in entities or any(touched(r["relative_path"]) for r in rows)
        if section == "locations":
            for row in rows:
                if touched(row["entry_note_path"]) or touched(row.get("package_root")):
                    return True
                plan = json.loads(blobs.unpack(config, row["value"]))
                if any(touched(path) for path in plan.get("mapping", {}).values()):
                    return True
            return False
        values = [state.materialize(config, r) for r in rows]
        if section == "decisions":
            return any(r.get("job") == job["id"] for r in values)
        return any(touched(r.get("path")) or r.get("entity") in entities for r in values)

    sections = [(k, before[k], after[k], current[k]) for k in ("mocs", "identities", "locations")]
    sections += [
        (k, before["organization"][k], after["organization"][k], current["organization"][k])
        for k in state.ORG_TABLES
    ]
    for section, original, previous, now in sections:
        for key in set(original) | set(previous) | set(now):
            if canonical(original.get(key)) == canonical(now.get(key)):
                continue
            rows = [v[key] for v in (original, previous, now) if key in v]
            if owned(section, key, rows):
                if section == "mocs":
                    old, new = original.get(key), now.get(key)
                    members_before = old["members"] if old else {}
                    members_after = new["members"] if new else {}
                    expected = {k: list(v) for k, v in members_before.items()}
                    pending: dict[str, list[str] | None] = {}
                    for item in job["items"]:
                        actions = set(item.get("steps", []))
                        entity = item["entity"]
                        if "link:" + key in actions:
                            expected[entity] = sorted(set(expected.get(entity, [])) | {"system"})
                        if "unlink:" + key in actions:
                            expected.pop(entity, None)
                        if item.get("intent") == "link:" + key:
                            pending[entity] = sorted(set(expected.get(entity, [])) | {"system"})
                        if item.get("intent") == "unlink:" + key:
                            pending[entity] = None
                    for entity in set(expected) | set(members_after) | set(pending):
                        allowed = [expected.get(entity)]
                        if entity in pending:
                            allowed.append(pending[entity])
                        if members_after.get(entity) not in allowed:
                            raise IntegrityError("maintenance_undo_relationship_conflict")
                    if (
                        old
                        and new
                        and any(old[k] != new[k] for k in old if k not in {"revision", "members"})
                    ):
                        raise IntegrityError("maintenance_undo_relationship_conflict")
                continue
            # Normalize both before and after, so a later cancellation neither
            # reverts nor conflicts with these unrelated authors' records.
            if key in now:
                original[key] = previous[key] = now[key]
            else:
                original.pop(key, None)
                previous.pop(key, None)
    journal["after_state"] = current
