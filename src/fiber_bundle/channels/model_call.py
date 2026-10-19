"""Single-attempt query model calls using the application's credential budget."""

import time
from dataclasses import asdict, replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .. import database, model_budgets, model_gateway
from ..config import Config, InvalidInput, contained, make_directory
from ..inputs import canonical
from ..query_policy_store import write_private


def complete(
    config: Config, selected: Any, identity: str, stage: str, payload: Any, check: Any
) -> Any:
    # Leave enough room for reasoning models to produce the validated final JSON.
    policy = replace(selected, max_output_tokens=max(8192, selected.max_output_tokens))
    wire = model_gateway.prepare(policy, payload)
    call = str(uuid5(NAMESPACE_URL, "fiber-channel-query:" + identity + ":" + stage))
    check()
    with database.locked(config), database.connect(config.state_root) as db:
        if db.execute("SELECT 1 FROM model_configuration_calls WHERE id=?", (call,)).fetchone():
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
                    canonical({**asdict(policy), "purpose": "channel_query", "stage": stage}),
                    reservation,
                    time.time(),
                ),
            )
    try:
        result = model_gateway.complete(policy, call, wire)
        usage = result.get("usage", {})
        if any(
            type(usage.get(k)) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens")
        ):
            raise InvalidInput("channel_model_usage_unknown")
        with database.locked(config), database.connect(config.state_root) as db:
            with database.transaction(db):
                model_budgets.settle(
                    db, reservation, usage["prompt_tokens"] + usage["completion_tokens"], 0
                )
                db.execute(
                    "UPDATE model_configuration_calls SET state='succeeded',input_tokens=?,"
                    "output_tokens=? WHERE id=?",
                    (usage["prompt_tokens"], usage["completion_tokens"], call),
                )
        # A successful paid response must survive downstream parsing/source
        # rejection. Private cache is diagnostic only; it does not replay calls.
        home = contained(config.state_root, "channel-query-results")
        make_directory(home)
        write_private(home / (call + ".json"), canonical(result).encode())
        check()
        return result
    except Exception:
        # Unknown usage keeps its reservation. Never retry a possibly consumed call.
        with database.locked(config), database.connect(config.state_root) as db:
            db.execute(
                "UPDATE model_configuration_calls SET state='failed',"
                "error_code='channel_model_call_failed' "
                "WHERE id=? AND state='running'",
                (call,),
            )
        raise
