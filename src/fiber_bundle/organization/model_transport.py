"""Settle admitted calls independently; pausing frees the maintenance scheduler immediately."""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from .. import database, model_budgets, model_gateway
from ..config import InvalidInput
from . import store
from .model_unknown import UnknownCall, detail


def save_result(config: Any, job: Any, key: str, row: Any) -> None:
    current = store.get(config, "model_reviews", key)
    if not current or current["id"] == row["id"]:
        store.put(config, "model_reviews", key, job["vault_id"], row)
    else:
        # A late old attempt must never replace a separately authorized newer attempt.
        current["attempts"] = [a for a in current.get("attempts", []) if a["id"] != row["id"]]
        current["attempts"].append({k: v for k, v in row.items() if k != "attempts"})
        store.put(config, "model_reviews", key, job["vault_id"], current)


def exchange(
    config: Any, job: Any, effective: Any, key: str, row: Any, payload: Any
) -> tuple[bool, Any]:
    identity, reservation = row["id"], row["reservation"]
    try:
        reply = model_gateway.complete(effective, identity, payload)
        usage = reply.get("usage", {})
        if any(
            type(usage.get(k)) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens")
        ):
            raise InvalidInput("organization_model_usage_unknown")
        choice = reply["choices"][0]
        content = choice["message"].get("content")
        valid = isinstance(content, str) and bool(content.strip())
        with database.locked(config), database.connect(config.state_root) as db:
            with database.transaction(db):
                model_budgets.settle(
                    db, reservation, usage["prompt_tokens"] + usage["completion_tokens"], 0
                )
                db.execute(
                    "UPDATE model_configuration_calls SET state=?,input_tokens=?,output_tokens=?,"
                    "error_code=? WHERE id=?",
                    (
                        "succeeded" if valid else "failed",
                        usage["prompt_tokens"],
                        usage["completion_tokens"],
                        None if valid else "organization_model_response_invalid",
                        identity,
                    ),
                )
            row.update(
                state="succeeded" if valid else "invalid_response",
                usage=usage,
                finish_reason=choice.get("finish_reason"),
                retryable=not valid and choice.get("finish_reason") == "length",
            )
            if valid:
                row["reply"] = content
                row["evidence_validation"] = {"state": "pending"}
            save_result(config, job, key, row)
        return valid, content
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, InvalidInput)
            else "model_timeout"
            if isinstance(error, TimeoutError)
            else "organization_model_result_unknown"
        )
        with database.locked(config), database.connect(config.state_root) as db:
            db.execute(
                "UPDATE model_configuration_calls SET state='failed',error_code=? "
                "WHERE id=? AND state='running'",
                (code, identity),
            )
            if row["state"] == "sending":
                row.update(state="unknown", error_code=code, ended_at=time.time())
                save_result(config, job, key, row)
                raise UnknownCall(detail(db, key, row)) from error
        raise


def wait(config: Any, job: Any, operation: Callable[[], Any]) -> Any:
    result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def perform() -> None:
        try:
            result.put((True, operation()))
        except BaseException as error:
            result.put((False, error))

    # Provider timeout remains bounded by the existing protocol. No task/file
    # writes occur in this daemon, only the admitted call's ledger and budget.
    threading.Thread(target=perform, name="classification-transport", daemon=True).start()
    while True:
        try:
            success, value = result.get(timeout=0.1)
        except queue.Empty:
            active = store.get(config, "jobs", job["id"])
            if (
                active
                and active.get("maintenance")
                and (
                    active["state"] not in {"queued", "running"}
                    or active.get("generation", 0) != job.get("generation", 0)
                )
            ):
                raise InvalidInput("maintenance_cancelled") from None
            continue
        if success:
            return value
        raise value
