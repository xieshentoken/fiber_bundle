"""Read and verify compensation evidence under one audited read boundary.

All retained files and operation payloads are read afresh. Reconciliation and
journal persistence remain under the caller's writer lease.
"""

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

from .. import moc_file_journal
from ..config import IntegrityError
from ..storage_read_scope import audited
from . import maintenance_undo_files as files
from . import maintenance_undo_ownership as ownership
from . import store


def note_stages(config: Any) -> set[str]:
    home = config.state_root / "moc-note-files"
    return (
        {p.name for p in home.iterdir() if p.is_dir() and not p.is_symlink()}
        if home.exists()
        else set()
    )


def operation_rows(
    db: sqlite3.Connection, vault: str, journal: Any, *, owned_ids: set[str] | None = None
) -> Iterator[sqlite3.Row]:
    """Read compensation payloads only after selecting the task-owned operation IDs."""
    prior = set(journal["unit_operations"])
    owned = set(journal["operations"])
    identities = [
        row[0]
        for row in db.execute("SELECT id FROM vault_operations WHERE vault_id=?", (vault,))
        if (
            row[0] in owned_ids
            if owned_ids is not None
            else row[0] not in prior
            or row[0] in journal.get("unit_owned_operations", [])
            or ("unit_paths" not in journal and row[0] in owned)
        )
    ]
    # Keep each query below SQLite's parameter limit and preserve the original ID order.
    for start in range(0, len(identities), 128):
        batch = identities[start : start + 128]
        rows = {
            row["id"]: row
            for row in db.execute(
                "SELECT id,payload FROM vault_operations WHERE vault_id=? AND id IN ("
                + ",".join("?" for _ in batch)
                + ")",
                (vault, *batch),
            )
        }
        if len(rows) != len(batch):
            raise IntegrityError("maintenance_undo_operations_changed")
        for identity in batch:
            yield rows[identity]


@audited
def observe(config: Any, db: Any, job: Any, journal: Any, root: Any, owned: Any) -> None:
    candidates = [p.path / "candidate.md" for p in owned]
    candidates += [
        config.state_root / "moc-note-files" / n / "candidate.md"
        for n in note_stages(config) - set(journal["unit_stages"])
    ]
    unit_paths = set(journal.get("unit_paths", journal["files"]))
    expected = {path: v["after"] for path, v in journal["files"].items() if path in unit_paths}
    for p in owned:
        if p.plan_id not in {p["id"] for p in journal["plans"]}:
            journal["plans"].append({"id": p.plan_id, "moc": p.value["moc_id"]})
    for moc in job["mocs"]:
        latest = moc_file_journal.latest(moc_file_journal.plans(config, job["vault_id"], moc["id"]))
        if latest and latest.plan_id in {p.plan_id for p in owned}:
            actual = files.observe(root, latest.value["path"])
            if actual and actual["sha256"] == latest.value["after_sha256"]:
                expected[latest.value["path"]] = actual
    for item in job["items"]:
        record = store.get(config, "note_moc", job["vault_id"] + ":" + item["entity"])
        if record and record["path"] in expected:
            expected[record["path"]] = {
                "sha256": record["after_sha256"],
                "file_key": record["after_file_key"],
            }
    for moc in job["new_mocs"]:
        if moc.get("claim") and expected.get(moc["path"]) is None:
            actual = files.observe(root, moc["path"])
            if actual and actual["sha256"] == moc["sha256"]:
                expected[moc["path"]] = actual
    for operation in operation_rows(
        db, job["vault_id"], journal, owned_ids=ownership.operations(db, job, journal)
    ):
        if (
            "unit_paths" in journal
            and operation["id"] in journal["unit_operations"]
            and operation["id"] not in journal.get("unit_owned_operations", [])
        ):
            # Keep prior owned operation evidence without re-reading archived
            # bodies in every later unit. Cancellation checks those files afresh.
            continue
        value = json.loads(operation["payload"])
        if operation["id"] not in journal["operations"]:
            journal["operations"].append(operation["id"])
        for exchange in value.get("exchanges", {}).values():
            candidates.append(root / exchange["stage"])
        if "moves" in value and value.get("source_root"):
            for update in value["updates"]:
                path = (
                    value["moves"].get(update["path"], update["path"])
                    if value.get("move_applied")
                    else update["path"]
                )
                actual = files.observe(root, path)
                if actual and actual["sha256"] == update["after_sha256"]:
                    expected[path] = actual
            if value.get("move_applied"):
                move = next(
                    (m for m in journal["moves"] if m["source"] == value["source_root"]), None
                )
                if move:
                    move["files"] = {}
                    for p, new in value["moves"].items():
                        actual = files.observe(root, new)
                        update = next((u for u in value["updates"] if u["path"] == p), None)
                        wanted = (
                            update["after_sha256"] if update else value["source_files"][p]["sha256"]
                        )
                        if not actual or actual["sha256"] != wanted:
                            raise IntegrityError("maintenance_undo_move_conflict")
                        move["files"][p] = actual
                for old, new in value["moves"].items():
                    if old in expected:
                        expected[new] = expected.pop(old)
                        expected[old] = None
    backups = {}
    for path in candidates:
        evidence = files.observe(path.parent, path.name)
        if evidence:
            backups[json.dumps(evidence, sort_keys=True)] = str(path)
    for path, evidence in journal["files"].items():
        if path not in expected:
            continue
        actual = files.observe(root, path)
        wanted = expected.get(path)
        if actual != wanted:
            raise IntegrityError("maintenance_undo_file_conflict")
        evidence["after"] = actual
        if evidence["before"] and evidence["before"] != actual and not evidence.get("backup"):
            evidence["backup"] = backups.get(json.dumps(evidence["before"], sort_keys=True))
