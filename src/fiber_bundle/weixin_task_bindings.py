"""Explicit idempotent task submission with a journal intent committed before the main run."""

from dataclasses import asdict
from typing import Any
from uuid import uuid4

from . import answer_tasks, article_bindings, database, execution_ledger
from . import weixin_inbox as inbox
from . import weixin_store as handoff
from . import weixin_task_inputs as task_inputs
from .article_worker import CapturePolicy
from .config import Config, InvalidInput
from .execution_config import ExecutionConfig
from .inputs import canonical
from .objects import digest
from .query_routing_config import QueryRoutingConfig
from .weixin_events import Binding


def input_identity(
    data: answer_tasks.AnswerInput | answer_tasks.NoteAnswerInput | article_bindings.ArticleInput,
) -> str:
    if isinstance(data, answer_tasks.NoteAnswerInput):
        if data.submission_id is None:
            raise InvalidInput("note query submission identity required")
        return data.submission_id
    return data.receipt_id


def submit(
    config: Config,
    binding: Binding,
    batch_id: str,
    policy: ExecutionConfig | QueryRoutingConfig,
    *,
    capture_policy: CapturePolicy | None = None,
    parent_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Queue only. Policy fixes cost limits; this call never executes or reserves a model call."""
    policy.validate()
    data: answer_tasks.AnswerInput | answer_tasks.NoteAnswerInput | article_bindings.ArticleInput
    request: dict[str, Any]
    if isinstance(policy, QueryRoutingConfig) and capture_policy is not None:
        raise InvalidInput("local_query_article_conflict")
    if capture_policy is None:
        if parent_receipt_id is not None:
            raise InvalidInput("consultation uses the complete batch")
        data = task_inputs.prepare_answer(config, binding, batch_id)
        kind = "answer"
        request = {"receipt_id": input_identity(data), "request": answer_tasks.request_data(data)}
    else:
        data = task_inputs.prepare_article(
            config, binding, batch_id, capture_policy, parent_receipt_id=parent_receipt_id
        )
        kind = "article"
        request = {
            "receipt_id": data.receipt_id,
            "image": capture_policy.image,
            "timeout": capture_policy.timeout_seconds,
        }
    receipt_id = input_identity(data)
    with inbox.connection(config, binding) as journal:
        view = handoff.read_locked(journal, binding, batch_id)
        if view["batch_state"] != "selected" or view["store_state"] != "stored":
            raise InvalidInput("batch no longer ready for submission")
        plan = journal.execute(
            "SELECT plan_json FROM wx_store_handoffs WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        plan_digest = digest(plan.encode())
        encoded = canonical(request)
        with database.transaction(journal):
            journal.execute(
                "CREATE TABLE IF NOT EXISTS wx_task_bindings("
                "run_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,batch_id TEXT NOT NULL "
                "REFERENCES wx_batches(id),input_receipt_id TEXT NOT NULL,kind TEXT NOT NULL "
                "CHECK(kind IN ('article','answer')),policy_digest TEXT NOT NULL,"
                "plan_digest TEXT NOT NULL,request_json TEXT NOT NULL,"
                "UNIQUE(batch_id,input_receipt_id,kind)) STRICT"
            )
            previous = journal.execute(
                "SELECT * FROM wx_task_bindings WHERE batch_id=? AND input_receipt_id=? AND kind=?",
                (batch_id, receipt_id, kind),
            ).fetchone()
            if previous is not None:
                if (
                    previous["policy_digest"],
                    previous["plan_digest"],
                    previous["request_json"],
                ) != (policy.digest, plan_digest, encoded):
                    raise InvalidInput("task submission conflicts with saved binding")
                run_id = previous["run_id"]
            else:
                run_id = str(uuid4())
                journal.execute(
                    "INSERT INTO wx_task_bindings VALUES(?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        binding.account_id,
                        batch_id,
                        receipt_id,
                        kind,
                        policy.digest,
                        plan_digest,
                        encoded,
                    ),
                )
        db = database.connect(config.state_root)
        try:
            if isinstance(policy, QueryRoutingConfig):
                from . import local_query_tasks

                if not isinstance(data, (answer_tasks.AnswerInput, answer_tasks.NoteAnswerInput)):
                    raise InvalidInput("local_query_input_invalid")
                value = local_query_tasks.submit(
                    config, db, policy, binding.client_id, data, task_id=run_id
                )
                return {
                    "run_id": run_id,
                    "task_id": run_id,
                    "state": value["state"],
                    "kind": kind,
                    "batch_id": batch_id,
                    "input": asdict(data),
                    "published": False,
                    "execution_role": "local_query",
                }
            run = db.execute("SELECT * FROM execution_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                if isinstance(data, (answer_tasks.AnswerInput, answer_tasks.NoteAnswerInput)):
                    vault_id, version_id = answer_tasks.scope(config, db, binding.client_id, data)
                    execution_ledger.create_run(
                        config,
                        db,
                        policy,
                        binding.client_id,
                        vault_id,
                        version_id,
                        answer=data,
                        run_id=run_id,
                    )
                else:
                    source = article_bindings.receipt_source(
                        config, db, binding.client_id, data.receipt_id
                    )
                    execution_ledger.create_run(
                        config,
                        db,
                        policy,
                        binding.client_id,
                        source.vault_id,
                        source.version_id,
                        article=data,
                        run_id=run_id,
                    )
            run = execution_ledger.get_run(db, run_id)
            if run["client_id"] != binding.client_id or run["vault_id"] != binding.vault_id:
                raise InvalidInput("execution identity conflicts with binding")
            task = (
                answer_tasks.get(db, run_id)
                if kind == "answer"
                else article_bindings.get(db, run_id)
            )
            expected_receipt = (
                None if isinstance(data, answer_tasks.NoteAnswerInput) else receipt_id
            )
            if task is None or task["receipt_id"] != expected_receipt:
                raise InvalidInput("execution task conflicts with binding")
            execution_ledger.authorize(config, db, run)
            return {
                "run_id": run_id,
                "state": run["state"],
                "kind": kind,
                "batch_id": batch_id,
                "input": asdict(data),
                "published": False,
            }
        finally:
            db.close()
