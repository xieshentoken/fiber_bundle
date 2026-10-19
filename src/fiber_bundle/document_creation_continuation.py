"""Frozen child intents after a fixed run. No running prompt mutation or model replay."""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, database
from . import document_creation_binding as binding
from . import document_creation_contract as contract
from . import document_creation_ledger as ledger
from . import document_creation_preparation as preparation
from . import document_creation_tasks as tasks
from .config import Config, IntegrityError, InvalidInput, contained
from .execution_config import ExecutionConfig
from .inputs import canonical, text
from .objects import digest
from .query_routing_files import read


def _intent(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["intent_json"])
        if (
            not isinstance(value, dict)
            or value["schema"] != "document-continuation/v1"
            or (value["parent_task_id"], value["child_task_id"], value["client_id"])
            != (row["parent_task_id"], row["child_task_id"], row["client_id"])
            or contract.parse(canonical(value["plan"])).value() != value["plan"]
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, InvalidInput):
        raise IntegrityError("document_continuation_changed") from None
    if (
        canonical(value) != row["intent_json"]
        or digest(row["intent_json"].encode()) != row["intent_sha256"]
    ):
        raise IntegrityError("document_continuation_changed")
    return value


def enqueue(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    parent_task_id: str,
    amendment_id: str,
    addition: str,
    *,
    expected_revision: int,
    expected_sha256: str,
    updated_query_task_id: str | None = None,
    expected_update_sha256: str | None = None,
) -> dict[str, Any]:
    """Caller holds main lock. Interrupted child creation is reconciled by its frozen UUID."""
    text(amendment_id, limit=400)
    contract.requirement(addition)
    parent = (
        _frozen(config, main, client_id, parent_task_id)
        if updated_query_task_id is not None
        else tasks.plan(config, main, client_id, parent_task_id)
    )
    rebased = None
    if updated_query_task_id is not None:
        update = update_preview(config, main, client_id, parent_task_id, updated_query_task_id)
        if expected_update_sha256 != update["preview_sha256"]:
            raise InvalidInput("document_input_update_confirmation_required")
        rebased = _rebase(config, main, client_id, parent, updated_query_task_id)
    if (parent.revision, parent.sha256) != (expected_revision, expected_sha256):
        raise InvalidInput("document_plan_conflict")
    bound = binding.owned(main, client_id, parent_task_id)
    run, _ = binding.checked(main, bound)
    if run["state"] not in {"running", "succeeded"}:
        raise InvalidInput("document_parent_not_running_or_succeeded")
    row = main.execute(
        "SELECT * FROM document_creation_continuations WHERE parent_task_id=?", (parent_task_id,)
    ).fetchone()
    fingerprint = digest(
        canonical([amendment_id, addition, expected_sha256, expected_update_sha256]).encode()
    )
    if row is None:
        child = str(uuid4())
        base = rebased[-1] if rebased is not None else parent
        updated = contract.amend(base, addition, expected_sha256=base.sha256, phase="prepared")
        value = {
            "schema": "document-continuation/v1",
            "parent_task_id": parent_task_id,
            "child_task_id": child,
            "client_id": client_id,
            "parent_plan_sha256": parent.sha256,
            "submission_sha256": fingerprint,
            "plan": updated.value(),
            "updated_query_task_id": updated_query_task_id,
            "input_update_sha256": expected_update_sha256,
        }
        raw = canonical(value)
        with database.transaction(main):
            main.execute(
                "INSERT INTO document_creation_continuations VALUES (?,?,?,?,?,'queued',?)",
                (parent_task_id, child, client_id, raw, digest(raw.encode()), time.time()),
            )
        row = main.execute(
            "SELECT * FROM document_creation_continuations WHERE parent_task_id=?",
            (parent_task_id,),
        ).fetchone()
    assert row is not None
    value = _intent(row)
    if row["state"] == "cancelled":
        raise InvalidInput("document_continuation_cancelled")
    if value["submission_sha256"] != fingerprint:
        raise InvalidInput("document_continuation_exists; amend the queued child explicitly")
    # Cross-database intent-first: a crash never creates an unbound executable child.
    with ledger.connection(config, write=True) as side:
        assert side is not None
        with database.transaction(side):
            existing = side.execute(
                "SELECT * FROM tasks WHERE id=?", (row["child_task_id"],)
            ).fetchone()
            original = side.execute(
                "SELECT * FROM tasks WHERE id=? AND client_id=?", (parent_task_id, client_id)
            ).fetchone()
            if original is None:
                raise IntegrityError("document_parent_missing")
            plans = rebased if rebased is not None else ledger.versions(side, original)
            if existing is None:
                if (
                    side.execute(
                        "SELECT count(*) FROM tasks WHERE client_id=?", (client_id,)
                    ).fetchone()[0]
                    >= ledger.MAX_TASKS
                ):
                    raise InvalidInput("document_task_limit")
                now = time.time()
                side.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,'prepared',?,?)",
                    (
                        row["child_task_id"],
                        client_id,
                        "continuation:" + row["child_task_id"],
                        ledger.submission(
                            plans[0].query_task_id,
                            plans[0].requirements[0],
                            plans[0].requested_format,
                        ),
                        plans[0].query_task_id,
                        digest(plans[0].input_json.encode()),
                        parent.revision + 1,
                        contract.parse(canonical(value["plan"])).sha256,
                        now,
                        now,
                    ),
                )
                for n, plan in enumerate((*plans, contract.parse(canonical(value["plan"]))), 1):
                    ledger.append(
                        side, row["child_task_id"], plan, now, "submitted" if n == 1 else "amended"
                    )
            else:
                verified = ledger.versions(side, existing)
                if (
                    existing["client_id"] != client_id
                    or verified[parent.revision].sha256
                    != contract.parse(canonical(value["plan"])).sha256
                ):
                    raise IntegrityError("document_child_changed")
    return release(config, main, client_id, parent_task_id)


def release(
    config: Config, main: sqlite3.Connection, client_id: str, parent_task_id: str
) -> dict[str, Any]:
    bound = binding.owned(main, client_id, parent_task_id)
    row = main.execute(
        "SELECT * FROM document_creation_continuations WHERE parent_task_id=?", (parent_task_id,)
    ).fetchone()
    if row is None or row["client_id"] != client_id:
        raise auth.Denied(404)
    _intent(row)
    child = tasks.status(config, main, client_id, row["child_task_id"])
    run, _ = binding.checked(main, bound)
    if child["state"] == "cancelled":
        main.execute(
            "UPDATE document_creation_continuations SET state='cancelled' WHERE parent_task_id=?",
            (parent_task_id,),
        )
    elif row["state"] == "queued" and run["state"] == "succeeded":
        previous(config, main, client_id, row["child_task_id"], require_ready=False)
        main.execute(
            "UPDATE document_creation_continuations SET state='ready' WHERE parent_task_id=?",
            (parent_task_id,),
        )
    state = main.execute(
        "SELECT state FROM document_creation_continuations WHERE parent_task_id=?",
        (parent_task_id,),
    ).fetchone()[0]
    return {
        "parent_task_id": parent_task_id,
        "child": child,
        "state": state,
        "apply_at": "next_version",
        "original_input_unchanged": True,
    }


def previous(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    child_task_id: str,
    *,
    require_ready: bool = True,
) -> str | None:
    row = main.execute(
        "SELECT * FROM document_creation_continuations WHERE child_task_id=?", (child_task_id,)
    ).fetchone()
    if row is None:
        return None
    value = _intent(row)
    if row["client_id"] != client_id:
        raise auth.Denied(404)
    if row["state"] == "cancelled" or (require_ready and row["state"] != "ready"):
        raise InvalidInput("document_continuation_not_ready")
    parent = (
        _frozen(config, main, client_id, row["parent_task_id"])
        if value["updated_query_task_id"] is not None
        else tasks.plan(config, main, client_id, row["parent_task_id"])
    )
    if parent.sha256 != value["parent_plan_sha256"]:
        raise IntegrityError("document_parent_changed")
    bound = binding.owned(main, client_id, row["parent_task_id"])
    run, _ = binding.checked(main, bound)
    if run["state"] != "succeeded":
        raise InvalidInput("document_parent_not_finished")
    artifact = main.execute(
        "SELECT * FROM document_creation_artifacts WHERE run_id=?", (run["id"],)
    ).fetchone()
    metadata = json.loads(artifact["artifact_json"])
    data = read(
        contained(config.state_root, artifact["relative_path"]), contract.MAX_ARTIFACT_BYTES
    )
    if (
        digest(data) != metadata["artifact"]["sha256"]
        or len(data) != metadata["artifact"]["byte_count"]
    ):
        raise IntegrityError("document_parent_draft_changed")
    return data.decode()


def candidates(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    query_task_id: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    # Query-task scope carries the authenticated conversation binding. Never choose by recency.
    with ledger.connection(config) as side:
        ids = (
            []
            if side is None
            else [
                r[0]
                for r in side.execute(
                    "SELECT id FROM tasks WHERE client_id=? AND query_task_id=? "
                    "AND state='prepared'",
                    (client_id, query_task_id),
                )
            ]
        )
    values = [tasks.status(config, main, client_id, tid) for tid in ids]
    if task_id is not None:
        values = [v for v in values if v["task_id"] == task_id]
        if not values:
            raise auth.Denied(404)
    return {
        "state": "selected" if len(values) == 1 else "confirmation_required",
        "candidates": values,
    }


def _frozen(
    config: Config, main: sqlite3.Connection, client_id: str, task_id: str
) -> contract.CreationPlan:
    binding.owned(main, client_id, task_id)
    with ledger.connection(config) as side:
        if side is None:
            raise auth.Denied(404)
        row = side.execute(
            "SELECT * FROM tasks WHERE id=? AND client_id=?", (task_id, client_id)
        ).fetchone()
        if row is None or row["state"] != "prepared":
            raise InvalidInput("document_parent_cancelled")
        plan = ledger.versions(side, row)[-1]
    for vault_id in plan.value()["input"]["vault_ids"]:
        auth.grant(main, client_id, vault_id, "query")
    return plan


def _rebase(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    parent: contract.CreationPlan,
    query_task_id: str,
) -> tuple[contract.CreationPlan, ...]:
    first = preparation.prepare(
        config, main, client_id, query_task_id, parent.requirements[0], parent.requested_format
    )
    values = [first]
    for requirement in parent.requirements[1:]:
        values.append(
            contract.amend(
                values[-1], requirement, expected_sha256=values[-1].sha256, phase="prepared"
            )
        )
    return tuple(values)


def update_preview(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    parent_task_id: str,
    query_task_id: str,
) -> dict[str, Any]:
    parent = _frozen(config, main, client_id, parent_task_id)
    plan = _rebase(config, main, client_id, parent, query_task_id)[-1]
    value = {
        "schema": "document-input-update/v1",
        "parent_task_id": parent_task_id,
        "parent_plan_sha256": parent.sha256,
        "query_task_id": query_task_id,
        "old_input_sha256": digest(parent.input_json.encode()),
        "new_input_sha256": digest(plan.input_json.encode()),
        "candidate_plan_sha256": plan.sha256,
        "source_vault_ids": plan.value()["input"]["vault_ids"],
        "confirmation_required": True,
    }
    return {**value, "preview_sha256": digest(canonical(value).encode())}


def check_budget(main: sqlite3.Connection, task_id: str, model: ExecutionConfig) -> None:
    """One document's versions share task ceilings as well as the global daily pool."""
    from .execution_ledger import BudgetExceeded

    root = task_id
    seen = set()
    while root not in seen:
        seen.add(root)
        row = main.execute(
            "SELECT parent_task_id FROM document_creation_continuations WHERE child_task_id=?",
            (root,),
        ).fetchone()
        if row is None:
            break
        root = row[0]
    else:
        raise IntegrityError("document_continuation_cycle")
    ids = [root]
    for current in ids:
        row = main.execute(
            "SELECT child_task_id FROM document_creation_continuations WHERE parent_task_id=?",
            (current,),
        ).fetchone()
        if row:
            if row[0] in ids or len(ids) >= contract.MAX_REVISIONS:
                raise IntegrityError("document_continuation_cycle_or_limit")
            ids.append(row[0])
    marks = ",".join("?" for _ in ids)
    count, cost = main.execute(
        "SELECT count(*),coalesce(sum(coalesce(c.charged_nusd,c.reserved_nusd)),0) "
        "FROM model_calls c JOIN document_creation_runs d ON d.run_id=c.run_id "
        f"WHERE d.task_id IN ({marks})",
        ids,
    ).fetchone()
    if count >= model.max_calls or cost + model.reserve_nusd > model.task_limit_nusd:
        raise BudgetExceeded("document_version_budget_exhausted")


def versions(
    config: Config, main: sqlite3.Connection, client_id: str, root_task_id: str
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    ancestor = root_task_id
    ancestors: set[str] = set()
    while True:
        parent = main.execute(
            "SELECT * FROM document_creation_continuations WHERE child_task_id=?", (ancestor,)
        ).fetchone()
        if parent is None:
            break
        _intent(parent)
        if parent["client_id"] != client_id:
            raise auth.Denied(404)
        if ancestor in ancestors or len(ancestors) >= contract.MAX_REVISIONS:
            raise IntegrityError("document_continuation_cycle_or_limit")
        ancestors.add(ancestor)
        ancestor = parent["parent_task_id"]
    current = root_task_id
    for number in range(len(ancestors) + 1, contract.MAX_REVISIONS + 1):
        if current in [v["task_id"] for v in values]:
            raise IntegrityError("document_continuation_cycle")
        value = tasks.status(config, main, client_id, current)
        values.append({"document_version": number, **value})
        row = main.execute(
            "SELECT * FROM document_creation_continuations WHERE parent_task_id=?", (current,)
        ).fetchone()
        if row is None:
            return values
        _intent(row)
        current = row["child_task_id"]
    raise IntegrityError("document_continuation_limit")


def cancel_all(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    task_id: str,
    expected_revision: int,
    expected_sha256: str,
) -> dict[str, Any]:
    root = tasks.status(config, db, client_id, task_id)
    if (root["revision"], root["plan_sha256"]) != (expected_revision, expected_sha256):
        raise InvalidInput("document_plan_conflict")
    for value in reversed(versions(config, db, client_id, task_id)):
        tasks.cancel(
            config,
            db,
            client_id,
            value["task_id"],
            expected_revision=value["revision"],
            expected_sha256=value["plan_sha256"],
        )
    return tasks.status(config, db, client_id, task_id)
