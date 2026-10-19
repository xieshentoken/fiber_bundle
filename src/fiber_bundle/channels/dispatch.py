"""Durable single-attempt query and send dispatch; unknown sends require reconciliation."""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

from .. import auth, cloud_answer_tasks, database, local_query_tasks, query_jobs
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import query, store
from .services import policy as service_policy


def current(
    config: Config,
    main: sqlite3.Connection,
    db: sqlite3.Connection,
    identity: str,
    revision: int,
    *,
    processing: bool = False,
) -> dict[str, Any]:
    channel = store.get(db, identity)
    planned_pause = (
        processing
        and service_policy.get(config, identity, "query") is not None
        and channel["state"] in {"paused", "stopping", "error"}
    )
    if (channel["state"] != "running" and not planned_pause) or channel["revision"] != revision:
        raise InvalidInput("channel_scope_withdrawn")
    store.authorize(config, main, channel)
    return channel


def capture(
    config: Config,
    identity: str,
    revision: int,
    event_id: str,
    owner: str,
    body: str,
    context: str = "",
) -> bool:
    if not event_id or len(event_id) > 400 or len(context) > 8192:
        return False
    with database.locked(config), store.opened(config) as db:
        main = database.connect(config.state_root)
        try:
            from ..vault_relocation.guard import receiving

            vault_id = store.get(db, identity)["value"]["vault_id"]
            with receiving(vault_id):
                channel = current(config, main, db, identity, revision)
            if "query" not in service_policy.roles(channel):
                return False
            question = query.question(body, channel["value"].get("natural_questions", False))
            if question is None:
                return False
            return store.capture(db, channel, event_id, owner, question, context)
        finally:
            main.close()


def advance(config: Config, identity: str, revision: int, *, independent: bool = False) -> None:
    if service_policy.get(config, identity, "query") is not None and not independent:
        return
    with store.readonly(config) as side:
        model = side is not None and store.get(side, identity)["value"]["answer_mode"] == "model"
    if model:
        from .model_answer import advance as model_advance

        model_advance(config, identity, revision, independent=independent)
        return
    selected_service = service_policy.get(config, identity, "query")
    if selected_service and not selected_service["enabled"]:
        return
    with database.locked(config), store.opened(config) as db:
        main = database.connect(config.state_root)
        try:
            from ..vault_relocation.guard import operation

            selected = store.get(db, identity)
            if operation(main, selected["value"]["vault_id"]):
                return
            channel = current(config, main, db, identity, revision, processing=independent)
            event = db.execute(
                "SELECT * FROM events WHERE channel_id=? AND state='captured' "
                "AND revision=? AND created>=? ORDER BY created LIMIT 1",
                (identity, revision, service_policy.cutoff(config, identity, "query")),
            ).fetchone()
            if event:
                # Commit consumption before any model admission. Crashes never replay this event.
                db.execute(
                    "UPDATE events SET state='processing' WHERE channel_id=? AND event_id=?",
                    (identity, event["event_id"]),
                )
                value = json.loads(event["value"])
                settings = channel["value"]
                calls = 2 if settings["answer_mode"] == "local_cloud" else 1
                used = db.execute(
                    "SELECT coalesce(sum(json_extract(value,'$.model_calls_reserved')),0) "
                    "FROM events WHERE channel_id=? AND revision=?",
                    (identity, revision),
                ).fetchone()[0]
                if settings["answer_mode"] != "evidence":
                    if (
                        settings.get("policy_version", 2) != 2
                        and used + calls > settings["model_limit"]
                    ):
                        raise InvalidInput("channel_model_budget_exhausted")
                    value["model_calls_reserved"] = calls
                    db.execute(
                        "UPDATE events SET value=? WHERE channel_id=? AND event_id=?",
                        (canonical(value), identity, event["event_id"]),
                    )
                try:
                    value.update(
                        query.submit(
                            config,
                            main,
                            channel,
                            value["question"],
                            "channel:" + identity + ":" + event["event_id"],
                        )
                    )
                    state = "ready" if "result" in value else "processing"
                except Exception:
                    state = "failed"
                db.execute(
                    "UPDATE events SET state=?,value=? WHERE channel_id=? AND event_id=?",
                    (state, canonical(value), identity, event["event_id"]),
                )
            for event in db.execute(
                "SELECT * FROM events WHERE channel_id=? AND state='processing' "
                "AND revision=? AND created>=?",
                (identity, revision, service_policy.cutoff(config, identity, "query")),
            ).fetchall():
                value = json.loads(event["value"])
                if not value.get("job_id"):
                    # An interrupted admission is unresolved, never silently resubmitted.
                    db.execute(
                        "UPDATE events SET state='failed' WHERE channel_id=? AND event_id=?",
                        (identity, event["event_id"]),
                    )
                    continue
                job = query_jobs.get(main, channel["value"]["client_id"], value["job_id"])
                if job["state"] == "succeeded":
                    value["result"] = (
                        cloud_answer_tasks.deliver(
                            config, main, channel["value"]["client_id"], job["cloud_run_id"]
                        )
                        if job["cloud_run_id"]
                        else local_query_tasks.deliver(
                            config, main, channel["value"]["client_id"], value["task_id"]
                        )
                    )
                    db.execute(
                        "UPDATE events SET state='ready',value=? WHERE channel_id=? AND event_id=?",
                        (canonical(value), identity, event["event_id"]),
                    )
                elif job["state"] not in {"running", "reserved"}:
                    db.execute(
                        "UPDATE events SET state='failed' WHERE channel_id=? AND event_id=?",
                        (identity, event["event_id"]),
                    )
        finally:
            main.close()


def prepare_delivery(
    config: Config, identity: str, revision: int
) -> tuple[dict[str, Any], sqlite3.Row, dict[str, Any], str | dict[str, Any], dict[str, str]] | str:
    selected_service = service_policy.get(config, identity, "query")
    if selected_service and not selected_service["enabled"]:
        return "idle"
    with database.locked(config), store.opened(config) as db:
        main = database.connect(config.state_root)
        try:
            from ..vault_relocation.guard import operation

            selected = store.get(db, identity)
            if operation(main, selected["value"]["vault_id"]):
                return "waiting_for_vault_relocation"
            channel = current(config, main, db, identity, revision)
            if db.execute(
                "SELECT 1 FROM events WHERE channel_id=? AND state IN ('sending','unknown')",
                (identity,),
            ).fetchone():
                raise InvalidInput("channel_delivery_requires_reconciliation")
            event = db.execute(
                "SELECT * FROM events WHERE channel_id=? AND state='ready' AND revision=? "
                "AND created>=? ORDER BY created LIMIT 1",
                (identity, revision, service_policy.cutoff(config, identity, "query")),
            ).fetchone()
            if event is None:
                return "idle"
            sent = db.execute(
                "SELECT count(*) FROM events WHERE channel_id=? AND revision=? "
                "AND state IN ('sending','sent','unknown')",
                (identity, revision),
            ).fetchone()[0]
            if (
                channel["value"].get("policy_version", 2) != 2
                and sent >= channel["value"]["outbound_limit"]
            ):
                return "budget_exhausted"
            data = json.loads(event["value"])
            query.recheck(config, main, channel, data["result"])
            vault = auth.grant(
                main, channel["value"]["client_id"], channel["value"]["vault_id"], "query"
            )
            from .. import expert_media

            capability = channel["value"].get("expert_media", "unknown")
            body = query.render(
                data["result"],
                vault["name"],
                capability,
                lambda module, version, resource: expert_media.read(
                    config, main, channel["value"]["client_id"], module, version, resource
                ),
                platform=channel["value"]["platform"],
            )
            from ..expert_presentation import bindings

            media_bindings = (
                bindings(config, main, channel["value"]["client_id"], body["response"])
                if isinstance(body, dict)
                else {}
            )
            db.execute(
                "UPDATE events SET state='sending' WHERE channel_id=? AND event_id=?",
                (identity, event["event_id"]),
            )
        finally:
            main.close()
    return channel, event, data, body, media_bindings


async def deliver(
    config: Config,
    identity: str,
    revision: int,
    sender: Callable[[str, Any, str, Callable[[], None]], Awaitable[bool]],
) -> str:
    prepared = await asyncio.to_thread(prepare_delivery, config, identity, revision)
    if isinstance(prepared, str):
        return prepared
    channel, event, data, body, media_bindings = prepared
    from ..expert_presentation import bindings

    checked = False

    def recheck() -> None:
        with database.locked(config), store.opened(config) as db:
            main = database.connect(config.state_root)
            try:
                channel = current(config, main, db, identity, revision)
                if (
                    db.execute(
                        "SELECT state FROM events WHERE channel_id=? AND event_id=?",
                        (identity, event["event_id"]),
                    ).fetchone()[0]
                    != "sending"
                ):
                    raise InvalidInput("channel_send_withdrawn")
                query.recheck(config, main, channel, data["result"])
                if (
                    isinstance(body, dict)
                    and bindings(config, main, channel["value"]["client_id"], body["response"])
                    != media_bindings
                ):
                    raise InvalidInput("channel_expert_media_binding_changed")
            finally:
                main.close()

    def guard() -> None:
        nonlocal checked
        if checked:
            raise InvalidInput("channel_send_replay_forbidden")
        recheck()
        checked = True

    guard.recheck = recheck  # type: ignore[attr-defined]
    if channel["value"]["platform"] == "weixin":
        from .delivery_parts import attach

        attach(config, identity, event["event_id"], guard)

    try:
        success = await sender(channel["owner"], body, data.get("context", ""), guard)
        state = (
            "sent"
            if checked and success
            else (getattr(guard, "delivery_state", "unknown") if checked else "cancelled")
        )
    except Exception:
        state = "unknown" if checked else "cancelled"

    def settle() -> None:
        with database.locked(config), store.opened(config) as db:
            db.execute(
                "UPDATE events SET state=? WHERE channel_id=? AND event_id=? AND state='sending'",
                (state, identity, event["event_id"]),
            )

    await asyncio.to_thread(settle)
    return state


def archival_guard(config: Config, identity: str, revision: int) -> None:
    """Called inside the inbox's filesystem lease, before any durable receipt."""
    with store.opened(config) as db:
        main = database.connect(config.state_root)
        try:
            channel = current(config, main, db, identity, revision)
            if not channel["value"]["add"]:
                raise InvalidInput("channel_intake_withdrawn")
        finally:
            main.close()
