"""One explicit Vault scope and the same evidence/model contracts for all entry surfaces."""

import sqlite3
import time
from typing import Any
from uuid import uuid4

from .. import (
    auth,
    evidence_answers,
    local_query_tasks,
    query_job_launch,
    query_jobs,
    query_policy_store,
)
from ..answer_tasks import ScopedNoteAnswerInput
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import store

PREFIXES = ("查询本库：", "查询本库:")


def question(body: str, natural: bool = False) -> str | None:
    from .natural_query import question as parse

    return parse(body, natural)


def evidence(
    config: Config,
    db: sqlite3.Connection,
    channel: dict[str, Any],
    value: str,
    retrieval: str | None = None,
) -> dict[str, Any]:
    store.authorize(config, db, channel)
    settings = channel["value"]
    result = evidence_answers.answer(
        config,
        db,
        settings["client_id"],
        value,
        retrieval or value[:200],
        [settings["vault_id"]],
        5,
    )
    vault = auth.grant(db, settings["client_id"], settings["vault_id"], "query")
    result["vault_name"] = vault["name"]
    return result


def submit(
    config: Config, db: sqlite3.Connection, channel: dict[str, Any], value: str, request_id: str
) -> dict[str, Any]:
    store.authorize(config, db, channel)
    settings = channel["value"]
    if settings["answer_mode"] == "evidence":
        return {"result": evidence(config, db, channel, value)}
    route = query_policy_store.checked(config, settings["configuration_sha256"])
    task = local_query_tasks.submit(
        config,
        db,
        route,
        settings["client_id"],
        ScopedNoteAnswerInput(
            (settings["vault_id"],), value, value[:200], 5, request_id, mode="cited_draft"
        ),
    )
    job = query_jobs.submit(
        config,
        db,
        route,
        settings["client_id"],
        "local",
        task["task_id"],
        settings["answer_mode"] == "local_cloud",
    )
    query_job_launch.launch(config, settings["client_id"], job["id"])
    return {"task_id": task["task_id"], "job_id": job["id"], "client_id": settings["client_id"]}


def recheck(
    config: Config, main: sqlite3.Connection, channel: dict[str, Any], response: dict[str, Any]
) -> None:
    store.authorize(config, main, channel)
    if response.get("status") == "query_failed":
        return
    sources = response.get("sources", [])
    settings = channel["value"]
    if response.get("retrieval_queries"):
        from .natural_query import evidence as retrieve

        current = retrieve(
            config, main, channel, response["question"], response["retrieval_queries"]
        )
    else:
        current = evidence(
            config,
            main,
            channel,
            response.get("question", "来源核对"),
            response.get("retrieval_query"),
        )
    before = [(s.get("note_id"), s["sha256"], s["snippet"]) for s in sources]
    after = [(s.get("note_id"), s["sha256"], s["snippet"]) for s in current["sources"]]
    if before != after or response.get("searched_vault_ids") != [settings["vault_id"]]:
        raise InvalidInput("channel_source_changed")


def render(
    response: dict[str, Any],
    vault_name: str,
    capability: str = "unknown",
    reader: Any = None,
    *,
    platform: str = "feishu",
) -> str | dict[str, Any]:
    from ..expert_presentation import project
    from . import reply_text

    projected = project(response, capability, reader)
    result = reply_text.text(response)
    if response.get("expert_blocks") and capability in {"images", "full"}:
        if len(result.encode()) > 8000:
            raise InvalidInput("channel_answer_too_large")
        schema = (
            "fiber-expert-channel-output/v1"
            if platform == "weixin"
            else "fiber-expert-feishu-output/v1"
        )
        return {"schema": schema, "text": result, "response": projected}
    if response.get("expert_blocks"):
        # Only registered expert blocks receive media failure/capability placeholders.
        missing = reply_text.missing_media(projected)
        if missing:
            result += "\n\n" + missing
    # Never quietly truncate an answer or its sources into a misleading successful reply.
    if len(result.encode()) > 8000:
        raise InvalidInput("channel_answer_too_large")
    return result


def test(
    config: Config, main: sqlite3.Connection, identity: str, value: str, execute_model: bool
) -> dict[str, Any]:
    with store.opened(config) as db:
        channel = store.get(db, identity)
        from .operations import pending
        from .services import policy as service_policy

        if "query" not in service_policy.roles(channel):
            raise InvalidInput("channel_query_role_unavailable")
        service = service_policy.get(config, identity, "query")
        if execute_model and service and not service["enabled"]:
            raise InvalidInput("channel_query_service_paused")
        if channel["state"] == "removed":
            raise InvalidInput("channel_removed")
        if pending(db, identity):
            raise InvalidInput("channel_operation_pending")
    if execute_model:
        if channel["value"]["answer_mode"] == "evidence":
            raise InvalidInput("channel_model_not_selected")
        settings = channel["value"]
        if settings["answer_mode"] == "model":
            event_id = "gui:" + str(uuid4())
            with store.opened(config) as db:
                if not store.capture(db, channel, event_id, channel["owner"], value):
                    raise InvalidInput("channel_query_not_admitted")
            return {"event_id": event_id, "state": "queued"}
        calls = 2 if settings["answer_mode"] == "local_cloud" else 1
        event_id = "gui:" + str(uuid4())
        with store.opened(config) as db:
            used = db.execute(
                "SELECT coalesce(sum(json_extract(value,'$.model_calls_reserved')),0) "
                "FROM events WHERE channel_id=? AND revision=?",
                (identity, channel["revision"]),
            ).fetchone()[0]
            if settings.get("policy_version", 2) != 2 and used + calls > settings["model_limit"]:
                raise InvalidInput("channel_model_budget_exhausted")
            # Commit before admission; neither a crash nor a gateway restart replays this test.
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    event_id,
                    channel["revision"],
                    "gui_processing",
                    canonical({"model_calls_reserved": calls}),
                    time.time(),
                ),
            )
            try:
                result = submit(config, main, channel, value, "gui-channel:" + event_id)
            except Exception:
                db.execute(
                    "UPDATE events SET state='failed' WHERE channel_id=? AND event_id=?",
                    (identity, event_id),
                )
                raise
            db.execute(
                "UPDATE events SET value=? WHERE channel_id=? AND event_id=?",
                (canonical(result | {"model_calls_reserved": calls}), identity, event_id),
            )
            return result
    return {"result": evidence(config, main, channel, value)}
