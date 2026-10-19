"""Read-only batch authorization for derived task receipts, under the caller's storage lock."""

import json
import sqlite3

from . import auth, weixin_intents, weixin_note_queries
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .objects import digest
from .weixin_events import Binding


def authorize(
    config: Config, db: sqlite3.Connection, run: sqlite3.Row, task: sqlite3.Row, kind: str
) -> None:
    note_request = json.loads(task["request_json"]) if kind == "answer" else {}
    note_query = bool(
        kind == "answer"
        and task["input_kind"] == "notes"
        and isinstance(note_request.get("submission_id"), str)
        and note_request["submission_id"].startswith("weixin-task:")
    )
    receipt = db.execute(
        "SELECT event_id FROM receipts WHERE job_id=?", (task["receipt_id"],)
    ).fetchone()
    if not note_query and (receipt is None or not receipt["event_id"].startswith("weixin-task:")):
        return
    # No recursive storage lock: all mutation uses the same lock as the execution caller.
    path = contained(config.state_root, "weixin-inbox.sqlite3")
    try:
        journal = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        journal.row_factory = sqlite3.Row
        try:
            if journal.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise auth.Denied(404)
            bound = journal.execute(
                "SELECT t.*,b.state AS batch_state,b.action,b.question,h.state AS store_state,"
                "h.plan_sha256,h.plan_json,i.binding_json FROM wx_task_bindings t "
                "JOIN wx_batches b ON b.id=t.batch_id AND b.account_id=t.account_id "
                "JOIN wx_store_handoffs h ON h.batch_id=b.id "
                "JOIN bindings i ON i.account_id=t.account_id WHERE t.run_id=?",
                (run["id"],),
            ).fetchone()
            if bound is None:
                raise auth.Denied(404)
            identity = json.loads(bound["binding_json"])
            plan = json.loads(bound["plan_json"])
            allowed = ("consult",) if kind == "answer" else ("save_one", "save_separately")
            if kind == "answer" and plan.get("query_after_save") is True:
                route = weixin_intents.read_locked(journal, Binding(**identity), bound["batch_id"])
                if (
                    not weixin_intents.query_after_save(route, bound["action"], bound["question"])
                    or plan["question"] != bound["question"]
                    or json.loads(task["request_json"])["question"] != plan["question"]
                ):
                    raise auth.Denied(404)
                allowed = ("save_one", "save_separately")
            expected_receipt = task["receipt_id"]
            if note_query:
                route = weixin_intents.read_locked(journal, Binding(**identity), bound["batch_id"])
                expected_receipt = weixin_note_queries.identity(bound["batch_id"])
                if (
                    not weixin_note_queries.existing(route)
                    or not weixin_note_queries.existing(plan)
                    or plan.get("query_vault_id") != run["vault_id"]
                    or note_request["submission_id"] != expected_receipt
                    or note_request["question"] != weixin_note_queries.question(plan["question"])
                    or plan["question"] != bound["question"]
                    or task["receipt_id"] is not None
                ):
                    raise auth.Denied(404)
            actual = (
                {"receipt_id": expected_receipt, "request": json.loads(task["request_json"])}
                if kind == "answer"
                else {
                    "receipt_id": task["receipt_id"],
                    "image": task["worker_image"],
                    "timeout": task["worker_timeout"],
                }
            )
            if (
                bound["kind"] != kind
                or bound["input_receipt_id"] != expected_receipt
                or bound["policy_digest"] != run["config_digest"]
                or bound["request_json"] != canonical(actual)
                or bound["batch_state"] != "selected"
                or bound["store_state"] != "stored"
                or bound["action"] not in allowed
                or identity["client_id"] != run["client_id"]
                or identity["vault_id"] != run["vault_id"]
                or bound["plan_digest"] != bound["plan_sha256"]
                or digest(bound["plan_json"].encode()) != bound["plan_digest"]
            ):
                raise auth.Denied(404)
            auth.grant(db, run["client_id"], run["vault_id"], "query" if note_query else "add")
        finally:
            journal.close()
    except (sqlite3.Error, ValueError, KeyError, TypeError, IntegrityError):
        raise auth.Denied(404) from None
