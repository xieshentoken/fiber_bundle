"""Preview and commit explicit channel lifecycle intents, before asynchronous stop/cleanup."""

import json
import sqlite3
import subprocess
import sys
import time
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from .. import database, feishu_runtime, query_policy_store
from ..config import Config, InvalidInput, contained, make_directory
from ..inputs import canonical
from ..payloads import Payload
from ..query_http import Digest
from ..runtime_environment import child_environment
from . import control, health, legacy, operations, store

Action = Literal["pause", "unbind", "remove"]


class Preview(Payload):
    action: Action


class Start(Payload):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    configuration_sha256: Digest


class Request(Start):
    action: Action
    operation_id: str
    expected_instance_id: str | None

    @field_validator("operation_id", "expected_instance_id")
    @classmethod
    def uuid(cls, value: str | None) -> str | None:
        if value is not None and str(UUID(value)) != value:
            raise ValueError("canonical_uuid_required")
        return value


def runtime(config: Config, channel: dict[str, Any]) -> dict[str, Any]:
    return (
        feishu_runtime.inspect(legacy.home(config, channel))
        if channel.get("origin") == "legacy"
        else control.status(config, channel["id"])
    )


def preview(config: Config, identity: str, action: Action) -> dict[str, Any]:
    with store.opened(config) as db:
        channel = operations.entry(config, db, identity)
        if channel["state"] == "removed":
            raise InvalidInput("channel_removed")
        if operations.pending(db, identity):
            raise InvalidInput("channel_operation_pending")
        instance = runtime(config, channel)
        title = "既有飞书入口" if channel.get("origin") == "legacy" else channel["value"]["name"]
        text = {"pause": "暂停", "unbind": "解除绑定", "remove": "移除本机连接"}[action]
        outcome = {
            "pause": "保留配置、绑定和登录状态。",
            "unbind": "解除本入口本人配对；微信需重新扫码登录。飞书应用凭证保留。",
            "remove": "清理本入口专属配置和凭证。外部或共享凭证文件保留。",
        }[action]
        from .services.policy import managed

        independent_hint = (
            "已接收任务按独立服务设置继续；暂停处理请使用查询或保存服务按钮。"
            if action == "pause" and managed(config, identity)
            else ""
        )
        return {
            "id": identity,
            "action": action,
            "revision": channel["revision"],
            "configuration_sha256": health.fingerprint(channel),
            "instance_id": instance.get("instance_id"),
            "confirmation": (
                f"{text}“{title}”？\n先停止收发并确认 Hermes 退出，再完成操作。{outcome}\n"
                "笔记、原件、执行审计和已消耗额度保留。发送结果未知时须先核对；不会自动重发。"
                + independent_hint
            ),
        }


def begin(config: Config, main: sqlite3.Connection, identity: str, data: Request) -> dict[str, Any]:
    with store.opened(config) as db:
        previous = db.execute(
            "SELECT * FROM channel_operations WHERE id=?", (data.operation_id,)
        ).fetchone()
        if previous:
            result = operations.operation(previous)
            if result["channel_id"] != identity or result["value"]["request"] != data.model_dump():
                raise InvalidInput("channel_revision_conflict")
            return operations.public(result) or {}
        channel = operations.entry(config, db, identity)
        if channel["state"] == "removed":
            raise InvalidInput("channel_removed")
        if operations.pending(db, identity):
            raise InvalidInput("channel_operation_pending")
        instance = runtime(config, channel)
        if (
            channel["revision"] != data.expected_revision
            or health.fingerprint(channel) != data.configuration_sha256
            or instance.get("instance_id") != data.expected_instance_id
        ):
            raise InvalidInput("channel_revision_conflict")
        old = channel.get("origin") == "legacy"
        value = {
            "request": data.model_dump(),
            "origin": "legacy" if old else "gui",
            "configuration_sha256": data.configuration_sha256,
            "channel": channel,
            "signal_reserved": False,
        }
        now = time.time()
        with database.transaction(db):
            operations.set_entry_state(
                db,
                channel,
                {"pause": "stopping", "unbind": "unbinding", "remove": "removing"}[data.action],
            )
            db.execute(
                "INSERT INTO channel_operations VALUES(?,?,?,?,?,?,?,NULL)",
                (data.operation_id, identity, data.action, "pending", canonical(value), now, now),
            )
        operation = operations.get(db, data.operation_id)
    from .lifecycle_execution import advance

    if not advance(config, data.operation_id):
        launch(config, operation)
    with store.opened(config) as db:
        return operations.public(operations.get(db, data.operation_id)) or {}


def launch(config: Config, operation: dict[str, Any]) -> None:
    folder = contained(config.state_root, "configs/channel-operations")
    make_directory(folder)
    path = folder / (operation["id"] + ".json")
    query_policy_store.write_private(path, json.dumps(config.storage_configuration()).encode())
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fiber_bundle.channels.lifecycle_worker",
                "--project-root",
                str(config.project_root),
                "--configuration",
                str(path),
                "--operation-id",
                operation["id"],
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except OSError:
        with store.opened(config) as db:
            operations.update(db, operation, "needs_review", "channel_operation_interrupted")


def resume(config: Config, identity: str, operation_id: str) -> dict[str, Any]:
    with store.opened(config) as db:
        operation = operations.get(db, operation_id)
        if operation["channel_id"] != identity:
            raise InvalidInput("channel_revision_conflict")
        if operation["state"] == "interrupted":
            raise InvalidInput("channel_restored_confirmation_required")
        if operation["state"] != "completed":
            operations.update(db, operation, "pending")
            launch(config, operation)
        return operations.public(operations.get(db, operation_id)) or {}
