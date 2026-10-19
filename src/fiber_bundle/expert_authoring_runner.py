"""Explicit resumable Pi chapter authoring and deterministic compiler handoff."""

import argparse
import io
import json
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from . import (
    automatic_policy,
    database,
    expert_authoring,
    expert_tasks,
    expert_tool_versions,
    source_imports,
    source_processing_policy,
)
from .config import Config, InvalidInput, contained, make_directory
from .executor_container import run_lock
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private
from .query_routing_files import read
from .runtime_environment import child_environment


def launch(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> dict[str, Any]:
    row = expert_authoring.prepare(config, db, cid, identity)
    if row["state"] not in {"queued", "waiting_budget", "failed", "interrupted"}:
        return source_imports.projection(db, source_imports.get(config, db, cid, identity))
    with run_lock(config, row["id"]):
        expert_authoring.save(db, row, "launching")
        folder = contained(config.state_root, "configs/expert-authoring-runs")
        make_directory(folder)
        path = folder / (row["id"] + ".json")
        write_private(
            path,
            canonical(
                {"storage": config.storage_configuration(), "client_id": cid, "identity": identity}
            ).encode(),
        )
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "fiber_bundle.expert_authoring_runner",
                    "--project-root",
                    str(config.project_root),
                    "--configuration",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_environment(),
                start_new_session=True,
            )
        except OSError:
            expert_authoring.save(db, row, "interrupted")
            raise
    return source_imports.projection(db, source_imports.get(config, db, cid, identity))


def run(config: Config, cid: str, identity: str) -> None:
    with database.locked(config), database.connect(config.state_root) as db:
        row = expert_authoring.lookup(db, identity)
        if row is None:
            raise InvalidInput("expert_authoring_unavailable")
    with run_lock(config, row["id"]):
        try:
            advance(config, cid, identity)
        except Exception as error:
            with database.locked(config), database.connect(config.state_root) as db:
                current = expert_authoring.lookup(db, identity)
                if current and current["state"] not in {"complete", "cancelled"}:
                    from .processing_errors import MESSAGES

                    current["value"]["error_code"] = (
                        str(error) if str(error) in MESSAGES else "expert_authoring_failed"
                    )
                    expert_authoring.save(db, current, "failed")
            raise


def advance(config: Config, cid: str, identity: str) -> None:
    def guard() -> None:
        with database.locked(config), database.connect(config.state_root) as db:
            current = expert_authoring.lookup(db, identity)
            if current is None or current["state"] not in {"running", "launching", "queued"}:
                raise InvalidInput("expert_authoring_stopped")
            expert_authoring.authorize(config, db, current)
            current_task = expert_tasks.get(db, identity)
            if current_task is None or current_task["state"] == "cancelled":
                raise InvalidInput("expert_authoring_stopped")
            expert_tool_versions.verify(config, current["value"]["tool_sha256"])

    with database.locked(config), database.connect(config.state_root) as db:
        row = expert_authoring.lookup(db, identity)
        if row is None:
            raise InvalidInput("expert_authoring_unavailable")
        source, plan, file = expert_authoring.authorize(config, db, row)
        value = row["value"]
        expert_authoring.save(db, row, "running")
        task = expert_tasks.get(db, identity)
        assert task is not None
        packed = expert_tasks.workpack(
            config, db, cid, source["vault_id"], task["workpack_import_id"]
        )
        version = db.execute(
            "SELECT source_version_id FROM receipts WHERE job_id=?", (source["receipt_id"],)
        ).fetchone()[0]
    tools, units = expert_tool_versions.load(config, value["tool_sha256"])
    if not value.get("seed_path"):
        root, seed = tools.seed(
            config,
            file,
            plan,
            json.loads(source["request_json"]).get("expert_profile", "native-text"),
            guard,
            packed,
        )
        value["seed_path"] = str(root.relative_to(config.state_root))
        value["seed_sha256"] = digest(canonical(seed).encode())
        value["total_units"] = len(seed["units"])
        value["known_gaps"] = list(seed.get("known_gaps", []))
        with database.locked(config), database.connect(config.state_root) as db:
            expert_authoring.save(db, row, "running")
    root = contained(config.state_root, value["seed_path"])
    seed = json.loads(read(root / "output/result.json", 4 * 1024 * 1024))
    if digest(canonical(seed).encode()) != value["seed_sha256"]:
        raise InvalidInput("expert_authoring_source_changed")
    model = automatic_policy.processing_route(config, value["vault_policy"]["settings"])
    for unit in seed["units"]:
        guard()
        uid = unit["unit_id"]
        draft_path = contained(root, "output/workpack/drafts/" + uid + ".json")
        saved = value["units"].get(uid)
        if saved and saved.get("state") == "complete":
            if digest(read(draft_path, 1024 * 1024)) != saved["sha256"]:
                raise InvalidInput("expert_authoring_proposal_invalid")
            continue
        if draft_path.exists():
            # Preserve supplied proposals. The canonical validator still checks them before compile.
            value["units"][uid] = {
                "state": "complete",
                "sha256": digest(read(draft_path, 1024 * 1024)),
                "source": "supplied_workpack",
            }
            continue
        if not units.author(
            config, row, unit, seed["draft_schema"], root, model, version, file.sha256, guard
        ):
            return

    guard()
    value["validation"] = tools.validate(config, root, plan, guard)
    if seed.get("visual_tasks", "").strip():
        value["known_gaps"].append("visual_tasks_not_human_reviewed")
    # Source-bound private workpack archive; final shareable Draft excludes this private input.
    buffer = io.BytesIO()
    from .expert_artifacts import tree

    entries = tree(root / "output/workpack")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            path = contained(root / "output/workpack", name)
            if path.is_symlink():
                raise InvalidInput("expert_authoring_proposal_invalid")
            archive.writestr(name, entries[name])
    raw = buffer.getvalue()
    with database.locked(config), database.connect(config.state_root) as db:
        expert_authoring.authorize(config, db, row)
        settings = source_processing_policy.inspect(config, None)
        imported = source_imports.create(
            config,
            db,
            {
                "client_id": cid,
                "vault_id": source["vault_id"],
                "submission_id": row["id"],
                "filename": "pi-workpack.zip",
                "source_sha256": digest(raw),
                "source_bytes": len(raw),
                "configuration_sha256": settings["configuration_sha256"],
                "action": "archive",
                "engine": "ordinary",
                "profile": "native",
                "references": {},
                "overrides": {},
            },
        )
        for ordinal, offset in enumerate(range(0, len(raw), source_imports.CHUNK_BYTES)):
            import base64

            chunk = raw[offset : offset + source_imports.CHUNK_BYTES]
            source_imports.chunk(
                config,
                db,
                cid,
                imported["id"],
                ordinal,
                digest(chunk),
                base64.b64encode(chunk).decode(),
            )
        source_imports.finish(config, db, cid, imported["id"])
        value["workpack_import_id"] = imported["id"]
        db.execute(
            "UPDATE source_expert_tasks SET workpack_import_id=?,state='prepared',"
            "launch_state='launched',next_command='start',updated_at=? "
            "WHERE id=? AND state!='cancelled'",
            (imported["id"], time.time(), identity),
        )
        expert_authoring.save(db, row, "complete")
    from .expert_execution import execute

    execute(config, identity)
    with database.locked(config), database.connect(config.state_root) as db:
        from . import automatic_jobs

        for pending in db.execute(
            "SELECT id FROM automatic_jobs WHERE kind='expert' AND source_id=? "
            "AND state='needs_review'",
            (identity,),
        ).fetchall():
            job = automatic_jobs.get(db, pending[0])
            if job["reason"] in {
                "expert_authoring_budget_required",
                "expert_authoring_model_reconciliation_required",
            }:
                automatic_jobs.authorize(config, db, job)
                automatic_jobs.settle(db, job, "waiting", "review")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = json.loads(read(args.configuration, 65536))
        storage = data["storage"]
        config = Config(
            args.project_root,
            state_relative=storage["state_root"],
            vaults=tuple(storage["vaults"].items()),
            vault_roots=tuple(storage.get("vault_roots", {}).items()),
        )
        config.validate()
        run(config, data["client_id"], data["identity"])
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
