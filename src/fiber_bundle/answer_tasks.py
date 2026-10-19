"""Durable receipt and note answers; host owns scope, evidence and validated output."""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import (
    answer_generation,
    answer_scope,
    auth,
    evidence_answers,
    vaults,
    weixin_note_queries,
    weixin_task_guard,
)
from .answer_generation import AnswerMode
from .answer_scope import ScopedNoteAnswerInput as ScopedNoteAnswerInput
from .config import Config, IntegrityError, InvalidInput
from .store import Store

PROMPT = "Return the host-bound answer JSON. No tools or additional statements."


@dataclass(frozen=True)
class AnswerInput:
    receipt_id: str
    session_id: str
    question: str
    retrieval_query: str
    limit: int = 10
    submission_id: str | None = None
    mode: AnswerMode = field(default="extractive", kw_only=True)


@dataclass(frozen=True)
class NoteAnswerInput:
    vault_id: str
    question: str
    retrieval_query: str
    limit: int = 10
    submission_id: str | None = None
    mode: AnswerMode = field(default="extractive", kw_only=True)


def scope(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    data: AnswerInput | NoteAnswerInput | ScopedNoteAnswerInput,
) -> tuple[str, str | None]:
    if isinstance(data, ScopedNoteAnswerInput):
        return answer_scope.resolve(config, db, client_id, data.vault_ids)[0], None
    if isinstance(data, NoteAnswerInput):
        vaults.checked_vault(config, auth.grant(db, client_id, data.vault_id, "query"))
        return data.vault_id, None
    bound = source(config, db, client_id, data.receipt_id, data.session_id)
    return bound["vault_id"], bound["source_version_id"]


def request_data(data: AnswerInput | NoteAnswerInput | ScopedNoteAnswerInput) -> dict[str, Any]:
    request: dict[str, Any] = dict(
        question=data.question,
        retrieval_query=data.retrieval_query,
        session_id=data.session_id if isinstance(data, AnswerInput) else None,
        limit=data.limit,
    )
    if answer_generation.mode({"mode": data.mode}) != "extractive":
        request["mode"] = data.mode
    if isinstance(data, ScopedNoteAnswerInput):
        request["scope_request"] = None if data.vault_ids is None else list(data.vault_ids)
    if data.submission_id is not None:
        request["submission_id"] = data.submission_id
    return request


def get(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row = db.execute("SELECT * FROM answer_tasks WHERE run_id=?", (run_id,)).fetchone()
    return row if isinstance(row, sqlite3.Row) else None


def source(
    config: Config, db: sqlite3.Connection, client_id: str, receipt_id: str, session_id: str
) -> sqlite3.Row:
    row = db.execute(
        "SELECT r.source_version_id,s.vault_id FROM receipts r "
        "JOIN source_versions v ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
        "JOIN session_refs sr ON sr.version_id=v.id "
        "WHERE r.job_id=? AND r.owner_client_id=? AND r.ingress_scope=? AND r.state='stored' "
        "AND sr.session_scope=? AND sr.session_id=?",
        (receipt_id, client_id, "client:" + client_id, "client:" + client_id, session_id),
    ).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    vaults.checked_vault(config, auth.grant(db, client_id, row["vault_id"], "query"))
    manifest = Store(config).verify_receipt_locked(db, receipt_id)
    if (manifest["session_scope"], manifest["session_id"]) != ("client:" + client_id, session_id):
        raise auth.Denied(404)
    return row


def snapshot(response: dict[str, Any]) -> list[dict[str, Any]]:
    # Original-download links are permission-dependent presentation, not evidence identity.
    return [{k: v for k, v in item.items() if k != "originals"} for item in response["sources"]]


def retrieve(
    config: Config, db: sqlite3.Connection, run: sqlite3.Row, request: dict[str, Any]
) -> dict[str, Any]:
    return evidence_answers.answer(
        config,
        db,
        run["client_id"],
        request["question"],
        request["retrieval_query"],
        answer_scope.stored(run, request),
        request["limit"],
        session_id=request["session_id"],
    )


def prepare_binding(
    config: Config,
    db: sqlite3.Connection,
    run: sqlite3.Row,
    data: AnswerInput | NoteAnswerInput | ScopedNoteAnswerInput,
) -> tuple[dict[str, Any], object]:
    """Freeze the shared answer contract, independently of its execution ledger."""
    if scope(config, db, run["client_id"], data) != (run["vault_id"], run["version_id"]):
        raise auth.Denied(404)
    request = request_data(data)
    if isinstance(data, ScopedNoteAnswerInput):
        request["vault_ids"] = answer_scope.resolve(config, db, run["client_id"], data.vault_ids)
    response = retrieve(config, db, run, request)
    evidence = answer_generation.snapshot(
        request,
        weixin_note_queries.snapshot(
            request, answer_scope.evidence_snapshot(request, snapshot(response))
        ),
    )
    return request, evidence


def bind(
    config: Config,
    db: sqlite3.Connection,
    run: sqlite3.Row,
    data: AnswerInput | NoteAnswerInput | ScopedNoteAnswerInput,
) -> None:
    request, evidence = prepare_binding(config, db, run, data)
    db.execute(
        "INSERT INTO answer_tasks "
        "(run_id,receipt_id,request_json,snapshot_json,result_json,input_kind) "
        "VALUES (?,?,?,?,NULL,?)",
        (
            run["id"],
            data.receipt_id if isinstance(data, AnswerInput) else None,
            json.dumps(request),
            json.dumps(evidence),
            "receipt" if isinstance(data, AnswerInput) else "notes",
        ),
    )


def authorize(config: Config, db: sqlite3.Connection, run: sqlite3.Row, task: sqlite3.Row) -> None:
    weixin_task_guard.authorize(config, db, run, task, "answer")
    request = json.loads(task["request_json"])
    answer_generation.mode(request)
    if task["input_kind"] == "notes":
        if (
            run["binding_kind"] != "notes"
            or run["version_id"] is not None
            or task["receipt_id"] is not None
            or request["session_id"] is not None
        ):
            raise auth.Denied(404)
        ids = answer_scope.stored(run, request)
        answer_scope.resolve(config, db, run["client_id"], tuple(ids))
        return
    if task["input_kind"] != "receipt" or run["binding_kind"] != "source":
        raise auth.Denied(404)
    bound = source(config, db, run["client_id"], task["receipt_id"], request["session_id"])
    if (bound["vault_id"], bound["source_version_id"]) != (run["vault_id"], run["version_id"]):
        raise auth.Denied(404)


def current(
    config: Config, db: sqlite3.Connection, run: sqlite3.Row, task: sqlite3.Row
) -> dict[str, Any]:
    authorize(config, db, run, task)
    request = json.loads(task["request_json"])
    response = retrieve(config, db, run, request)
    if answer_generation.snapshot(
        request,
        weixin_note_queries.snapshot(
            request, answer_scope.evidence_snapshot(request, snapshot(response))
        ),
    ) != json.loads(task["snapshot_json"]):
        raise IntegrityError("answer evidence changed; create a new task")
    return response


def model_request(
    config: Config, db: sqlite3.Connection, run: sqlite3.Row, task: sqlite3.Row
) -> dict[str, Any]:
    if db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (run["id"],)).fetchone():
        raise InvalidInput("answer model call cannot be replayed")
    response = current(config, db, run, task)
    return answer_generation.model_request(json.loads(task["request_json"]), response)


def accept(config: Config, db: sqlite3.Connection, run: sqlite3.Row, body: dict[str, Any]) -> None:
    task = get(db, run["id"])
    if task is None:
        raise InvalidInput("missing answer binding")
    response = current(config, db, run, task)
    result = answer_generation.accept(json.loads(task["request_json"]), body, response["sources"])
    db.execute(
        "UPDATE answer_tasks SET result_json=? WHERE run_id=? AND result_json IS NULL",
        (json.dumps(result), run["id"]),
    )


def deliver(config: Config, db: sqlite3.Connection, client_id: str, run_id: str) -> dict[str, Any]:
    run = db.execute(
        "SELECT * FROM execution_runs WHERE id=? AND client_id=?", (run_id, client_id)
    ).fetchone()
    task = get(db, run_id)
    if run is None or task is None:
        raise auth.Denied(404)
    response = current(config, db, run, task)
    if run["state"] != "succeeded" or task["result_json"] is None:
        raise InvalidInput("answer result not ready")
    answer_generation.deliver(
        json.loads(task["request_json"]), json.loads(task["result_json"]), response
    )
    response.update(model_used=True, run_id=run_id)
    return response
