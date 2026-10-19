"""Byte-bounded proposal fragments with durable paid-run reuse and canonical unit assembly."""

import json
from pathlib import Path
from typing import Any

from . import database, execution, execution_ledger, expert_authoring, pi_processing
from .config import Config, InvalidInput, contained, make_directory
from .execution_config import ExecutionConfig
from .expert_proposals import combine, fragments, validate
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private
from .query_routing_files import read


def author(
    config: Config,
    row: dict[str, Any],
    unit: dict[str, Any],
    schema: dict[str, Any],
    root: Path,
    model: ExecutionConfig,
    version: str,
    source_sha: str,
    guard: Any,
) -> bool:
    value = row["value"]
    uid = unit["unit_id"]
    saved = value["units"].setdefault(uid, {"state": "pending", "parts": {}})
    proposals = []
    for number, fragment in enumerate(fragments(unit)):
        guard()
        part = saved["parts"].get(str(number))
        folder = root / "proposals"
        make_directory(folder)
        target = contained(folder, uid + f"-{number}.json")
        if part and part.get("state") == "complete":
            raw = read(target, 1024 * 1024)
            if digest(raw) != part["sha256"]:
                raise InvalidInput("expert_authoring_proposal_invalid")
            proposals.append(json.loads(raw))
            continue
        if not fragment["focus_spans"]:
            proposal = {
                "schema_version": "tkc.semantic-draft/v0.1",
                "unit_id": uid,
                "proposer_instance": "fiber-host:structure-only",
                "candidate_disposition": {
                    "action": "context-only",
                    "reason": "No extractable focus text; no knowledge authored.",
                },
                "objects": [],
                "relations": [],
                "conflicts": [],
                "gaps": [],
            }
        else:
            with database.locked(config), database.connect(config.state_root) as db:
                if part is None:
                    rid = pi_processing.prepare(
                        config,
                        db,
                        model,
                        value["client_id"],
                        row["vault_id"],
                        version,
                        "expert",
                        source_sha,
                        [
                            {
                                "id": uid,
                                "title": unit["title"],
                                "focus_spans": fragment["focus_spans"],
                                "context_spans": [],
                            }
                        ],
                        allow_cloud=value["vault_policy"]["settings"]["allow_model_source_content"],
                        semantic_contract={
                            "draft_schema": schema,
                            "unit_id": uid,
                            "visual_task_ids": unit["visual_task_ids"],
                            "scope": "this fragment only; propose, never review/Gold",
                        },
                        project_skill=value.get("project_skill"),
                    )
                    part = {"run_id": rid, "state": "pending"}
                    saved["parts"][str(number)] = part
                    expert_authoring.save(db, row, "running")
                state = execution_ledger.get_run(db, part["run_id"])["state"]
            if state in {"queued", "waiting_budget"}:
                execution.execute(config, model, part["run_id"], "fixed expert chapter skill")
            guard()
            with database.locked(config), database.connect(config.state_root) as db:
                state = execution_ledger.get_run(db, part["run_id"])["state"]
                if state != "succeeded":
                    value["error_code"] = (
                        "expert_authoring_budget_required"
                        if state == "waiting_budget"
                        else "expert_authoring_model_reconciliation_required"
                    )
                    expert_authoring.save(
                        db, row, "waiting_budget" if state == "waiting_budget" else "failed"
                    )
                    return False
                draft = pi_processing.result(config, db, part["run_id"])
            proposal = validate(draft["semantic_workpack"], fragment, part["run_id"])
            value["known_gaps"] = sorted(set(value["known_gaps"] + draft["gaps"]))
        raw = canonical(proposal).encode()
        if target.exists() and read(target, 1024 * 1024) != raw:
            raise InvalidInput("expert_authoring_proposal_invalid")
        write_private(target, raw)
        saved["parts"].setdefault(str(number), {}).update(state="complete", sha256=digest(raw))
        proposals.append(proposal)
        with database.locked(config), database.connect(config.state_root) as db:
            expert_authoring.save(db, row, "running")
    result = combine(unit, proposals)
    target = contained(root, "output/workpack/drafts/" + uid + ".json")
    make_directory(target.parent)
    raw = canonical(result).encode()
    write_private(target, raw)
    saved.update(state="complete", sha256=digest(raw))
    with database.locked(config), database.connect(config.state_root) as db:
        expert_authoring.save(db, row, "running")
    return True
