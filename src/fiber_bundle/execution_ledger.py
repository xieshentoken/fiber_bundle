"""Run authorization, persistent transitions and atomic cost reservations."""

import sqlite3
import time
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fiber_bundle import (
    answer_tasks,
    article_bindings,
    auth,
    cloud_answer_binding,
    database,
    document_bindings,
    vaults,
)
from fiber_bundle.config import Config, InvalidInput
from fiber_bundle.execution_config import ExecutionConfig


class BudgetExceeded(InvalidInput):
    pass


if TYPE_CHECKING:
    from .source_task_guard import SourceBinding


def authorize(config: Config, db: sqlite3.Connection, run: sqlite3.Row) -> None:
    cloud = cloud_answer_binding.get(db, run["id"])
    if cloud is not None:
        cloud_answer_binding.current(config, db, run, cloud)
        return
    answer = answer_tasks.get(db, run["id"])
    if answer is not None:
        answer_tasks.authorize(config, db, run, answer)
        return
    if run["binding_kind"] not in {"source", "document"}:
        raise auth.Denied(404)
    document = document_bindings.get(db, run["id"])
    if document is not None:
        document_bindings.authorize(config, db, run, document)
        return
    article = article_bindings.get(db, run["id"])
    if article is not None:
        article_bindings.authorize(config, db, run, article)
        return
    client = db.execute("SELECT active FROM clients WHERE id=?", (run["client_id"],)).fetchone()
    if client is None or not client[0]:
        raise auth.Denied(401)
    for permission in ("query", "original"):
        vaults.checked_vault(config, auth.grant(db, run["client_id"], run["vault_id"], permission))
    if not db.execute(
        "SELECT 1 FROM source_versions v JOIN sources s ON s.id=v.source_id "
        "WHERE v.id=? AND s.vault_id=?",
        (run["version_id"], run["vault_id"]),
    ).fetchone():
        raise auth.Denied(404)


def get_run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM execution_runs WHERE id=?", (run_id,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise InvalidInput("unknown execution")
    return row


def create_run(
    config: Config,
    db: sqlite3.Connection,
    policy: ExecutionConfig,
    client_id: str,
    vault_id: str,
    version_id: str | None,
    *,
    article: article_bindings.ArticleInput | None = None,
    document: document_bindings.DocumentInput | None = None,
    answer: answer_tasks.AnswerInput
    | answer_tasks.NoteAnswerInput
    | answer_tasks.ScopedNoteAnswerInput
    | None = None,
    run_id: str | None = None,
    source_binding: "SourceBinding | None" = None,
) -> str:
    policy.validate()
    if sum(binding is not None for binding in (article, answer, document)) > 1:
        raise InvalidInput("execution has multiple bindings")
    notes = isinstance(answer, (answer_tasks.NoteAnswerInput, answer_tasks.ScopedNoteAnswerInput))
    if notes != (version_id is None):
        raise InvalidInput("execution source binding mismatch")
    run_id = str(uuid4()) if run_id is None else str(UUID(run_id))
    with database.transaction(db):
        db.execute(
            "INSERT INTO execution_runs "
            "(id,client_id,vault_id,version_id,config_digest,state,attempts,task_limit_nusd,"
            "currency,created_at,updated_at,error_code,binding_kind) "
            "VALUES (?,?,?,?,?,'queued',0,?,?,?, ?,NULL,?)",
            (
                run_id,
                client_id,
                vault_id,
                version_id,
                policy.digest,
                policy.task_limit_nusd,
                policy.currency,
                time.time(),
                time.time(),
                "notes" if notes else "source",
            ),
        )
        run = get_run(db, run_id)
        if article is not None:
            article_bindings.bind(config, db, run, article)
        if document is not None:
            document_bindings.bind(config, db, run, document)
        if answer is not None:
            answer_tasks.bind(config, db, run, answer)
        if source_binding is not None:
            from .source_task_guard import bind

            bind(db, run, source_binding)
        authorize(config, db, run)
    return run_id


def transition(db: sqlite3.Connection, run_id: str, state: str, error: str | None = None) -> None:
    # Cancellation is terminal even if an in-flight request subsequently returns.
    db.execute(
        "UPDATE execution_runs SET state=?,error_code=?,updated_at=? "
        "WHERE id=? AND state NOT IN ('cancelled','succeeded')",
        (state, error, time.time(), run_id),
    )


def start(config: Config, db: sqlite3.Connection, run_id: str, policy: ExecutionConfig) -> None:
    with database.transaction(db):
        run = get_run(db, run_id)
        authorize(config, db, run)
        if run["binding_kind"] == "document":
            raise InvalidInput("deterministic document requires its dedicated runner")
        if run["config_digest"] != policy.digest:
            raise InvalidInput("execution configuration changed")
        if run["state"] not in ("queued", "interrupted", "waiting_budget"):
            raise InvalidInput("execution cannot start")
        if db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (run_id,)).fetchone():
            raise InvalidInput("prior model side effects require reconciliation")
        document = document_bindings.get(db, run_id)
        if document is not None and document["state"] != "queued":
            raise InvalidInput("document extraction already attempted")
        if run["attempts"] >= policy.max_attempts:
            raise InvalidInput("attempt limit reached")
        db.execute("UPDATE execution_runs SET attempts=attempts+1 WHERE id=?", (run_id,))
        transition(db, run_id, "running")


def check_budget(
    db: sqlite3.Connection,
    run_id: str,
    policy: ExecutionConfig,
    *,
    reserve_nusd: int | None = None,
    billing_day: str | None = None,
) -> None:
    """Read the same conservative totals used inside atomic reservation."""
    run = get_run(db, run_id)
    day = billing_day or datetime.now(ZoneInfo(policy.timezone)).date().isoformat()
    amount = policy.reserve_nusd if reserve_nusd is None else reserve_nusd

    def total(where: str, value: str) -> tuple[int, int]:
        row = db.execute(
            "SELECT count(*),coalesce(sum(coalesce(charged_nusd,reserved_nusd)),0) "
            "FROM model_calls WHERE " + where + "=?",
            (value,),
        ).fetchone()
        return int(row[0]), int(row[1])

    count, cost = total("run_id", run_id)
    daily_count, daily_cost = total("billing_day", day)
    from .agent_connections.cloud_budget import totals as agent_cloud_totals

    agent_count, agent_cost = agent_cloud_totals(db, day)
    daily_count += agent_count
    daily_cost += agent_cost
    from . import model_budgets

    shared = model_budgets.key(db, policy.secret_file) is not None
    processing = db.execute("SELECT 1 FROM pi_processing_runs WHERE run_id=?", (run_id,)).fetchone()
    if not shared and processing:
        raise model_budgets.Exhausted("processing_key_budget_unconfigured")
    if shared and (processing or hasattr(policy, "configuration_id")):
        # The atomic key reservation below is the single budget for modern model work.
        # Legacy explicit policies retain their independent limits.
        return
    if (
        count >= policy.max_calls
        or (not shared and daily_count >= policy.daily_calls)
        or cost + amount > run["task_limit_nusd"]
        or (not shared and daily_cost + amount > policy.daily_limit_nusd)
    ):
        raise BudgetExceeded("budget exhausted")


def reserve(
    config: Config,
    db: sqlite3.Connection,
    run_id: str,
    policy: ExecutionConfig,
    *,
    ceiling: ExecutionConfig | None = None,
) -> str:
    with database.transaction(db):
        run = get_run(db, run_id)
        authorize(config, db, run)
        if run["state"] != "running" or run["config_digest"] != policy.digest:
            raise InvalidInput("execution not active")

        day = datetime.now(ZoneInfo(policy.timezone)).date().isoformat()
        if ceiling is not None:
            if ceiling.timezone != policy.timezone:
                raise InvalidInput("budget timezone mismatch")
            check_budget(db, run_id, ceiling, reserve_nusd=policy.reserve_nusd, billing_day=day)
        check_budget(db, run_id, policy, billing_day=day)
        call_id = str(uuid4())
        from . import model_budgets

        reservation = model_budgets.reserve(
            db,
            policy.secret_file,
            policy.max_input_tokens + policy.max_output_tokens,
            policy.reserve_nusd,
        )
        if policy.reserve_nusd == 0 and reservation is None:
            raise InvalidInput("processing_key_budget_unconfigured")
        db.execute(
            "INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,'reserved',?)",
            (
                call_id,
                run_id,
                policy.model,
                policy.price_revision,
                day,
                policy.currency,
                policy.reserve_nusd,
                time.time(),
            ),
        )
        from .usage_credentials import record as record_credential

        record_credential(db, call_id, policy.secret_file)
        if reservation is not None:
            db.execute("INSERT INTO model_call_budget_bindings VALUES(?,?)", (call_id, reservation))
        return call_id


def settle(
    db: sqlite3.Connection,
    call_id: str,
    policy: ExecutionConfig,
    usage: object,
    *,
    reconcile: bool = False,
) -> None:
    if not isinstance(usage, dict):
        raise InvalidInput("missing usage")
    counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    if any(type(value) is not int or not 0 <= value <= 10**9 for value in counts):
        raise InvalidInput("invalid usage")
    incoming, outgoing = counts
    if not isinstance(incoming, int) or not isinstance(outgoing, int):
        raise InvalidInput("invalid usage")
    cost = incoming * policy.input_nusd + outgoing * policy.output_nusd
    from . import model_budgets

    reservation = db.execute(
        "SELECT reservation_id FROM model_call_budget_bindings WHERE call_id=?", (call_id,)
    ).fetchone()
    if reservation is not None:
        model_budgets.settle(db, reservation[0], incoming + outgoing, cost)
    # Cache discounts are deliberately not assumed; accounting is conservative.
    db.execute(
        "UPDATE model_calls SET state='settled',charged_nusd=?,input_tokens=?,output_tokens=? "
        "WHERE id=? AND (state='reserved' OR (? AND state='uncertain'))",
        (
            None if policy.price_revision == "unknown" else cost,
            incoming,
            outgoing,
            call_id,
            reconcile,
        ),
    )
    if incoming > policy.max_input_tokens or outgoing > policy.max_output_tokens:
        raise InvalidInput("provider exceeded reserved limits")


def interrupt(db: sqlite3.Connection, run_id: str) -> None:
    db.execute(
        "UPDATE model_calls SET state='uncertain' WHERE run_id=? AND state='reserved'", (run_id,)
    )
    transition(db, run_id, "interrupted", "execution_interrupted")
