"""Single-attempt query admission and recovery of verifiable paid responses."""

import time
from dataclasses import asdict, replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import database, model_budgets, model_gateway
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import model_call_results as results


def _settle(config: Config, call: str, snapshot: str, result: Any) -> None:
    incoming, outgoing = results.usage(result)
    with (
        database.locked(config),
        database.connect(config.state_root) as db,
        database.transaction(db),
    ):
        row = db.execute("SELECT * FROM model_configuration_calls WHERE id=?", (call,)).fetchone()
        if (
            row is None
            or row["policy_json"] != snapshot
            or row["state"] not in {"running", "succeeded"}
        ):
            raise InvalidInput("channel_model_result_unknown")
        if row["state"] == "succeeded":
            if (row["input_tokens"], row["output_tokens"]) != (incoming, outgoing):
                raise InvalidInput("channel_model_result_unknown")
            return
        model_budgets.settle(db, row["reservation_id"], incoming + outgoing, 0)
        db.execute(
            "UPDATE model_configuration_calls SET state='succeeded',input_tokens=?,"
            "output_tokens=? WHERE id=?",
            (incoming, outgoing, call),
        )


def complete(
    config: Config, selected: Any, identity: str, stage: str, payload: Any, check: Any
) -> Any:
    policy = replace(selected, max_output_tokens=max(8192, selected.max_output_tokens))
    wire = model_gateway.prepare(policy, payload)
    request_digest = results.digest(wire)
    snapshot = canonical(
        {
            **asdict(policy),
            "purpose": "channel_query",
            "stage": stage,
            "request_sha256": request_digest,
            **({"query_scope": check.query_scope} if getattr(check, "query_scope", None) else {}),
        }
    )
    call = str(uuid5(NAMESPACE_URL, "fiber-channel-query:" + identity + ":" + stage))
    check()
    with database.locked(config), database.connect(config.state_root) as db:
        prior = db.execute("SELECT * FROM model_configuration_calls WHERE id=?", (call,)).fetchone()
        if prior:
            if prior["policy_json"] != snapshot or prior["state"] not in {"running", "succeeded"}:
                raise InvalidInput("channel_model_result_unknown")
        else:
            if results.exists(config, call):
                # An orphaned response can still represent a paid request.
                raise InvalidInput("channel_model_result_unknown")
            with database.transaction(db):
                reservation = model_budgets.reserve(
                    db, policy.secret_file, policy.max_input_tokens + policy.max_output_tokens, 0
                )
                if reservation is None:
                    raise InvalidInput("processing_key_budget_unconfigured")
                credential = db.execute(
                    "SELECT credential_id FROM model_configurations WHERE id=?",
                    (policy.configuration_id,),
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO model_configuration_calls "
                    "VALUES(?,?,?,?,?,'running',?,?,NULL,NULL,NULL,NULL,?)",
                    (
                        call,
                        policy.configuration_id,
                        credential,
                        policy.provider,
                        policy.model,
                        snapshot,
                        reservation,
                        time.time(),
                    ),
                )
    if prior:
        result = results.load(config, call, policy.digest, request_digest)
    else:
        try:
            result = model_gateway.complete(policy, call, wire)
            # Persist the response BEFORE settlement. A storage timeout or process
            # interruption may then settle it once, without contacting the provider.
            results.save(config, call, policy.digest, request_digest, result)
        except Exception:
            # Unknown usage retains the reservation; there is no automatic network retry.
            with database.locked(config), database.connect(config.state_root) as db:
                db.execute(
                    "UPDATE model_configuration_calls SET state='failed',"
                    "error_code='channel_model_call_failed' "
                    "WHERE id=? AND state='running'",
                    (call,),
                )
            raise
    _settle(config, call, snapshot, result)
    check()
    return result
