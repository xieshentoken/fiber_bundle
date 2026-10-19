"""At-most-once file delivery and failure notice, bound to the registered owner and draft."""

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from . import database, weixin_inbox
from . import document_creation_artifacts as artifacts
from . import document_creation_binding as binding
from .config import Config, IntegrityError, InvalidInput
from .inputs import AssetInput, canonical
from .objects import digest
from .weixin_events import Binding

FileSender = Callable[[str, AssetInput, Callable[[str], None]], Awaitable[bool]]
TextSender = Callable[[str, str, Callable[[], None]], Awaitable[bool]]
STAGES = ("allocate", "upload", "send")


def _bound(config: Config, owner: Binding, client_id: str) -> None:
    if owner.client_id != client_id:
        raise InvalidInput("document_delivery_owner_mismatch")
    with weixin_inbox.connection(config, owner) as journal:
        weixin_inbox.bound(journal, owner)


def _checked(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["intent_json"])
    except (ValueError, TypeError):
        raise IntegrityError("document_delivery_changed") from None
    if (
        not isinstance(value, dict)
        or canonical(value) != row["intent_json"]
        or digest(row["intent_json"].encode()) != row["intent_sha256"]
        or value.get("run_id") != row["run_id"]
        or value.get("schema") != "document-file-delivery/v1"
    ):
        raise IntegrityError("document_delivery_changed")
    return value


def status(db: sqlite3.Connection, client_id: str, task_id: str) -> dict[str, Any]:
    bound = binding.owned(db, client_id, task_id)
    row = db.execute(
        "SELECT * FROM document_creation_deliveries WHERE run_id=?", (bound["run_id"],)
    ).fetchone()
    if row is None:
        return {"state": "not_requested", "local_retained": True}
    _checked(row)
    return {
        "state": row["state"],
        "notice_state": row["notice_state"],
        "local_retained": True,
        "automatic_retry": False,
        "regeneration": False,
    }


async def deliver(
    config: Config,
    owner: Binding,
    task_id: str,
    sender: FileSender,
    notice: TextSender | None = None,
    *,
    expected_artifact_sha256: str,
    max_bytes: int,
    allow_delivery: bool = False,
) -> dict[str, Any]:
    if allow_delivery is not True:
        raise InvalidInput("document_file_delivery_not_enabled")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 8 * 1024 * 1024:
        raise InvalidInput("document_upload_limit_invalid")
    _bound(config, owner, owner.client_id)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            raw, metadata = artifacts.get(config, db, owner.client_id, task_id)
            if digest(raw) != expected_artifact_sha256 or len(raw) > max_bytes:
                raise InvalidInput("document_upload_draft_or_limit_changed")
            asset = AssetInput(
                raw, metadata["artifact"]["original_name"], metadata["artifact"]["media_type"]
            )
            value = {
                "schema": "document-file-delivery/v1",
                "run_id": metadata["run_id"],
                "binding": asdict(owner),
                "artifact": metadata["artifact"],
                "max_bytes": max_bytes,
            }
            encoded = canonical(value)
            with database.transaction(db):
                row = db.execute(
                    "SELECT * FROM document_creation_deliveries WHERE run_id=?",
                    (metadata["run_id"],),
                ).fetchone()
                if row is not None:
                    if canonical(_checked(row)) != encoded:
                        raise IntegrityError("document_delivery_binding_changed")
                    return status(db, owner.client_id, task_id)
                db.execute(
                    "INSERT INTO document_creation_deliveries VALUES(?,?,?,'sending','pending',?)",
                    (metadata["run_id"], encoded, digest(encoded.encode()), time.time()),
                )
        finally:
            db.close()
    called: list[str] = []

    def authorize(stage: str) -> None:
        if stage not in STAGES or tuple(called) != STAGES[: STAGES.index(stage)]:
            raise InvalidInput("document_transport_must_not_retry")
        _bound(config, owner, owner.client_id)
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                data, current = artifacts.get(config, main, owner.client_id, task_id)
                row = main.execute(
                    "SELECT * FROM document_creation_deliveries WHERE run_id=?",
                    (metadata["run_id"],),
                ).fetchone()
                if (
                    row["state"] != "sending"
                    or canonical(_checked(row)) != encoded
                    or current != metadata
                    or data != raw
                ):
                    raise IntegrityError("document_delivery_changed")
            finally:
                main.close()
        called.append(stage)

    state = "unknown"
    try:
        async with asyncio.timeout(60):
            result = await sender(owner.owner_id, asset, authorize)
            if result is True and tuple(called) == STAGES:
                state = "sent"
            elif result is False:
                state = "failed"
    except asyncio.CancelledError:
        raise  # Durable sending claim survives; no network effect is replayed.
    except Exception:
        pass  # Provider bodies, tokens and private paths are never recorded as errors.
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            db.execute(
                "UPDATE document_creation_deliveries SET state=?,notice_state=? WHERE run_id=? "
                "AND state='sending'",
                (state, "not_needed" if state == "sent" else "pending", metadata["run_id"]),
            )
        finally:
            db.close()
    if state != "sent":
        await notify_failure(config, owner, task_id, notice)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return status(db, owner.client_id, task_id)
        finally:
            db.close()


async def notify_failure(
    config: Config, owner: Binding, task_id: str, sender: TextSender | None
) -> None:
    _bound(config, owner, owner.client_id)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            bound = binding.owned(db, owner.client_id, task_id)
            row = db.execute(
                "SELECT * FROM document_creation_deliveries WHERE run_id=?", (bound["run_id"],)
            ).fetchone()
            if (
                row is None
                or row["state"] not in {"failed", "unknown"}
                or row["notice_state"] != "pending"
            ):
                return
            value = _checked(row)
            if value["binding"] != asdict(owner):
                raise IntegrityError("document_notice_owner_changed")
            db.execute(
                "UPDATE document_creation_deliveries SET notice_state=? WHERE run_id=?",
                ("unavailable" if sender is None else "sending", bound["run_id"]),
            )
        finally:
            db.close()
    if sender is None:
        return
    checked = False

    def guard() -> None:
        nonlocal checked
        if checked:
            raise InvalidInput("document_notice_must_not_retry")
        _bound(config, owner, owner.client_id)
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                artifacts.get(config, main, owner.client_id, task_id)
                current = main.execute(
                    "SELECT * FROM document_creation_deliveries WHERE run_id=?", (bound["run_id"],)
                ).fetchone()
                if (
                    current is None
                    or current["notice_state"] != "sending"
                    or current["state"] not in {"failed", "unknown"}
                    or _checked(current) != value
                ):
                    raise IntegrityError("document_notice_changed")
            finally:
                main.close()
        checked = True

    state = "unavailable"
    try:
        async with asyncio.timeout(30):
            if (
                await sender(
                    owner.owner_id,
                    "文档文件交付失败或结果未知；未核验草稿已保留在本机。不会自动重试或重新生成，可查询任务状态。",
                    guard,
                )
                is True
                and checked
            ):
                state = "sent"
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
    with database.locked(config):
        main = database.connect(config.state_root)
        try:
            main.execute(
                "UPDATE document_creation_deliveries SET notice_state=? WHERE run_id=? "
                "AND notice_state='sending'",
                (state, bound["run_id"]),
            )
        finally:
            main.close()


def reconcile(db: sqlite3.Connection, client_id: str, task_id: str) -> dict[str, Any]:
    bound = binding.owned(db, client_id, task_id)
    with database.transaction(db):
        db.execute(
            "UPDATE document_creation_deliveries SET state='unknown' WHERE run_id=? "
            "AND state='sending'",
            (bound["run_id"],),
        )
        db.execute(
            "UPDATE document_creation_deliveries SET notice_state='unavailable' WHERE run_id=? "
            "AND notice_state='sending'",
            (bound["run_id"],),
        )
    return status(db, client_id, task_id)
