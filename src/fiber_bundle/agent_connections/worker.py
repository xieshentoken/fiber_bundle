"""Finite probe/query operation. MCP supplies evidence; HTTP revalidates; Hermes generates."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .. import database
from ..config import Config
from ..inputs import canonical
from . import cloud_budget, files, hermes, mcp_client, models, store
from .contracts import TurnRequest
from .http_client import Client, ClientError
from .turn import PreparedTurn, TurnBlocked, run


async def answer(
    transport: Path,
    request: TurnRequest,
    applied: dict[str, Any],
    model: dict[str, Any],
    home: Path,
    output: Path,
) -> dict[str, Any]:
    client = Client(transport)
    initial = client.context()
    config = initial["configuration"]
    scope = tuple(request.vault_ids or config["vault_ids"])
    if (
        not initial["enabled"]
        or not set(scope) <= set(config["vault_ids"])
        or len(request.retrieval_queries) > config["limits"]["queries"]
    ):
        raise ClientError("agent_scope_or_limit_invalid")
    responses = {}
    trace = []
    async with mcp_client.session(transport) as mcp:
        for index, query in enumerate(request.retrieval_queries, 1):
            responses[query] = await mcp_client.call(
                mcp,
                "fiber_search",
                {
                    "query": query,
                    "vault_ids": list(scope),
                    "limit": config["limits"]["results_per_query"],
                },
            )
            trace.append(
                {
                    "event": "mcp_retrieval_completed",
                    "query_index": index,
                    "hits": len(responses[query]["results"]),
                }
            )
        if client.context() != initial:
            raise ClientError("agent_context_changed")
    generated = {}

    def generate(prepared: PreparedTurn) -> str:
        result = hermes.generate(
            prepared, applied, model, home, output, config["limits"]["timeout_seconds"]
        )
        generated.update(result)
        trace.extend(result["trace"])
        return str(result["answer"])

    def verify(prepared: PreparedTurn) -> None:
        client.verify(prepared)
        trace.append({"event": "http_source_verified"})

    result = run(
        request,
        read_context=client.context,
        retrieve=lambda query, *_: responses[query],
        verify_evidence=verify,
        generate=generate,
        should_stop=lambda: False,
    )
    return {
        "answer": result,
        "context": initial,
        "provider_calls": generated["provider_calls"],
        "usage": generated["usage"],
        "usage_state": generated["usage_state"],
        "trace": trace,
    }


def execute(job_file: Path) -> None:
    job = files.private_json(job_file, 8192)
    if not isinstance(job, dict):
        raise ValueError("agent_job_invalid")
    # Persisted job paths originate exclusively from the authenticated host launcher.
    config = Config(
        Path(job["project_root"]),
        job["storage"]["state_root"],
        tuple(sorted(job["storage"]["vaults"].items())),
        tuple(sorted(job["storage"].get("vault_roots", {}).items())),
    )
    identity, oid = job["connection_id"], job["operation_id"]
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            op = store.op(db, identity, oid)
            row = store.get(db, identity)
            if (
                op["state"] != "queued"
                or op["cancel_requested"]
                or op["revision"] != row["revision"]
            ):
                raise ValueError("agent_operation_invalid")
            store.finish(db, oid, "running")
            applied = json.loads(row["applied_json"])
            model = (
                json.loads(op["payload_json"]).get("model_snapshot")
                or models.resolve(config, store.spec(row).model_configuration_id)
                if op["kind"] == "test_query"
                else None
            )
            kind = op["kind"]
        finally:
            db.close()
    started = time.monotonic()
    try:
        transport = files.folder(config, identity) / "transport.json"
        if kind == "probe":
            result = asyncio.run(mcp_client.probe(transport))
        else:
            assert model is not None
            request = TurnRequest.model_validate(job["request"])
            result = asyncio.run(
                answer(
                    transport,
                    request,
                    applied,
                    model,
                    files.folder(config, identity) / "hermes-home",
                    files.folder(config, identity) / (oid + ".generation.json"),
                )
            )
        client = Client(transport)
        if result["context"] != client.context():
            raise ClientError("agent_context_changed")
        if kind == "test_query":
            from .turn import PreparedTurn

            client.verify(
                PreparedTurn(
                    oid,
                    canonical(result["context"]),
                    canonical(result["answer"]["sources"]),
                    "",
                    "",
                )
            )
        if kind == "test_query":
            result["trace"].append({"event": "final_delivery_verified"})
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                op = store.op(db, identity, oid)
                row = store.get(db, identity)
                if (
                    op["state"] != "running"
                    or op["cancel_requested"]
                    or row["revision"] != op["revision"]
                ):
                    raise ClientError("agent_cancelled")
                if kind == "test_query":
                    files.put(config, identity, oid + ".answer.json", result)
                    summary = {
                        "state": "passed",
                        "context": result["context"],
                        "provider_calls": result["provider_calls"],
                        "usage": result["usage"],
                        "usage_state": result["usage_state"],
                        "trace": result["trace"],
                        "operation_id": oid,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "capability": "retrieval_before_answer_verified",
                    }
                    reservation = json.loads(op["payload_json"]).get("cloud_reservation")
                    if reservation is not None:
                        cost = cloud_budget.charged(reservation, result["usage"])
                        summary["cloud_cost_nusd"] = (
                            None if reservation.get("price_revision") == "unknown" else cost
                        )
                        if result["usage"] is not None:
                            from .. import model_budgets

                            model_budgets.settle(
                                db,
                                reservation.get("budget_reservation_id"),
                                result["usage"]["prompt_tokens"]
                                + result["usage"]["completion_tokens"],
                                cost,
                            )
                        summary["currency"] = "USD"
                    column = "test_json"
                else:
                    summary = result
                    column = "probe_json"
                db.execute(
                    "UPDATE agent_connections SET " + column + "=? WHERE id=?",
                    (canonical(summary), identity),
                )
                store.finish(db, oid, "succeeded", summary)
            finally:
                db.close()
    except Exception as exc:
        code = exc.code if isinstance(exc, (ClientError, TurnBlocked)) else "agent_operation_failed"
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                op = store.op(db, identity, oid)
                if op["state"] in ("queued", "running"):
                    row = store.get(db, identity)
                    if (
                        row["revision"] == op["revision"]
                        and row["credential_generation"] == op["credential_generation"]
                    ):
                        column = "test_json" if kind == "test_query" else "probe_json"
                        db.execute(
                            "UPDATE agent_connections SET " + column + "=? WHERE id=?",
                            (
                                canonical(
                                    {
                                        "state": "failed",
                                        "context": store.context(config, db, identity),
                                        "error_code": code,
                                        "operation_id": oid,
                                    }
                                ),
                                identity,
                            ),
                        )
                    store.finish(
                        db,
                        oid,
                        "failed",
                        hermes.diagnostic(
                            files.folder(config, identity) / (oid + ".generation.json")
                        )
                        if kind == "test_query"
                        else None,
                        error=code,
                    )
            finally:
                db.close()
