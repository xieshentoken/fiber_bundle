"""Explicit bounded model tests with persistent admission and no knowledge content."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

from .. import database, model_budgets, model_gateway
from ..config import Config
from ..execution_config import parse_execution_config
from ..inputs import canonical
from . import policy, store
from .contracts import Test
from .errors import ModelError


def status(db: Any, identity: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT id,state,input_tokens,output_tokens,error_code FROM "
        "model_configuration_calls WHERE id=?",
        (identity,),
    ).fetchone()
    if row is None:
        raise ModelError("model_unconfigured")
    result = dict(row)
    if row["error_code"]:
        result["message"] = ModelError(row["error_code"]).message
    return result


class Runtime:
    def __init__(self, config: Config):
        self.config = config
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fiber-model-test")
        with database.connect(config.state_root) as db:
            db.execute(
                "UPDATE model_configuration_calls SET "
                "state='interrupted',error_code='model_test_interrupted' WHERE state IN "
                "('queued','running')"
            )

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)

    def start(self, db: Any, identity: str, request: Test) -> dict[str, Any]:
        row = store.get(db, identity)
        if row["revision"] != request.expected_revision:
            raise ModelError("model_revision_conflict")
        if any(r["kind"] == "asr" for r in store.references(db, identity)):
            raise ModelError("model_test_audio_unsupported")
        if not request.approve_tokens:
            raise ModelError("model_test_approval_required")
        if db.execute(
            "SELECT 1 FROM model_configuration_calls WHERE state IN ('queued','running')"
        ).fetchone():
            raise ModelError("model_test_busy")
        selected = replace(
            policy.resolve(self.config, identity),
            max_input_tokens=512,
            max_output_tokens=128,
            timeout_seconds=15,
        )
        cid = row["credential_id"]
        call_id = str(uuid4())
        with database.transaction(db):
            reservation = model_budgets.reserve(db, selected.secret_file, 640, 0)
            assert reservation is not None
            db.execute(
                "INSERT INTO model_configuration_calls "
                "VALUES(?,?,?,?,?,'queued',?,?,NULL,NULL,NULL,NULL,?)",
                (
                    call_id,
                    identity,
                    cid,
                    selected.provider,
                    selected.model,
                    canonical(asdict(selected)),
                    reservation,
                    time.time(),
                ),
            )
        self.pool.submit(self.run, call_id)
        return status(db, call_id)

    def run(self, call_id: str) -> None:
        config = self.config
        with database.locked(config), database.connect(config.state_root) as db:
            row = db.execute(
                "SELECT * FROM model_configuration_calls WHERE id=?", (call_id,)
            ).fetchone()
            if not row or row["state"] != "queued":
                return
            selected = parse_execution_config(json.loads(row["policy_json"]))
            db.execute(
                "UPDATE model_configuration_calls SET state='running' WHERE id=?", (call_id,)
            )
        try:
            reply = model_gateway.complete(
                selected,
                call_id,
                model_gateway.prepare(
                    selected, {"messages": [{"role": "user", "content": "Reply with OK."}]}
                ),
            )
            usage = reply.get("usage")
            if not isinstance(usage, dict) or any(
                type(usage.get(k)) is not int or usage[k] < 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                raise ModelError("model_response_invalid")
            with (
                database.locked(config),
                database.connect(config.state_root) as db,
                database.transaction(db),
            ):
                model_budgets.settle(
                    db,
                    row["reservation_id"],
                    usage["prompt_tokens"] + usage["completion_tokens"],
                    0,
                )
                db.execute(
                    "UPDATE model_configuration_calls SET "
                    "state='succeeded',input_tokens=?,output_tokens=? WHERE id=?",
                    (usage["prompt_tokens"], usage["completion_tokens"], call_id),
                )
                key = db.execute(
                    "SELECT generation FROM model_credentials WHERE id=?", (row["credential_id"],)
                ).fetchone()
                if key and key[0] == getattr(selected, "credential_generation", None):
                    verification = {
                        "state": "passed",
                        "observed_at": time.time(),
                        "credential_generation": key[0],
                        "configuration_revision": getattr(selected, "configuration_revision", None),
                    }
                    db.execute(
                        "UPDATE model_configurations SET verification_json=? "
                        "WHERE id=? AND revision=?",
                        (
                            canonical(verification),
                            row["configuration_id"],
                            getattr(selected, "configuration_revision", None),
                        ),
                    )
        except Exception as exc:
            code = exc.code if isinstance(exc, ModelError) else "model_test_failed"
            with database.locked(config), database.connect(config.state_root) as db:
                db.execute(
                    "UPDATE model_configuration_calls SET state='failed',error_code=? WHERE id=?",
                    (code, call_id),
                )
