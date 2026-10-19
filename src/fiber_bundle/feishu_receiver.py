"""Owner-only durable capture and bounded resource recovery for a separate Hermes adapter."""

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from . import (
    database,
    feishu_events,
    feishu_sync,
    weixin_batches,
    weixin_confirmations,
    weixin_inbox,
)
from . import weixin_recovery as recovery
from .config import Config, InvalidInput
from .objects import digest
from .weixin_events import MAX_BYTES, Binding, encoded


def initialize(config: Config, binding: Binding) -> None:
    if not binding.account_id.startswith("feishu:"):
        raise InvalidInput("explicit Feishu binding required")
    weixin_inbox.initialize(config, binding)
    weixin_confirmations.initialize(config, binding)
    recovery.initialize(config, binding)
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        from .expert_command_schema import initialize as initialize_commands
        from .intake_abort_store import initialize as initialize_abort

        initialize_commands(db)
        initialize_abort(db)
        db.execute(
            "CREATE TABLE IF NOT EXISTS fiber_feishu_downloads("
            "job_id TEXT NOT NULL REFERENCES events(job_id),item_key TEXT NOT NULL,"
            "state TEXT NOT NULL CHECK(state IN ('running','complete','failed')),"
            "PRIMARY KEY(job_id,item_key)) STRICT"
        )


def capture(
    config: Config,
    binding: Binding,
    payload: dict[str, Any],
    *,
    commands: bool = False,
    expert_authorization: str | None = None,
    expert_limit: int = 0,
    expert_unlimited: bool = False,
    guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Call under the adapter's callback lock, before any legacy filtering or dedup."""
    from .vault_relocation.guard import receiving

    with receiving(binding.vault_id):
        direct = payload.get("event", {}).get("message", {})
        try:
            organization_command = direct.get("message_type") == "text" and str(
                json.loads(direct.get("content", "{}")).get("text", "")
            ).startswith("/归类")
        except (ValueError, TypeError, AttributeError):
            organization_command = False
        message = feishu_events.normalize(
            binding,
            payload,
            commands=commands or expert_authorization is not None or organization_command,
        )
        if message is None:
            return {"state": "ignored"}
        from .intake_abort_commands import capture as capture_abort
        from .organization.commands import capture as capture_organization

        if capture_organization(config, binding, message, guard):
            return {"state": "organization_command_recorded"}
        if capture_abort(config, binding, message, guard):
            return {"state": "intake_abort_recorded"}
        if expert_authorization is not None:
            from .expert_feishu import capture as expert_capture

            if expert_capture(
                config,
                binding,
                message,
                expert_authorization,
                expert_limit,
                guard=guard,
                unlimited=expert_unlimited,
            ):
                return {"state": "expert_command_recorded"}
            from .expert_file_intents import select_file

            select_file(config, binding, message, expert_authorization, guard=guard)
        cursor = weixin_inbox.cursor(config, binding)
        result = weixin_inbox.receive(
            config,
            binding,
            cursor,
            {"get_updates_buf": digest(encoded(message)), "msgs": [message]},
            guard=guard,
        )
        return result


def advance(
    config: Config, binding: Binding, quiet_seconds: float = 3, *, intent_routing: bool = False
) -> None:
    from .intake_abort_commands import advance as stop_intake

    stop_intake(config, binding)
    from .organization.commands import advance as organize

    organize(config, binding)
    if intent_routing:
        weixin_confirmations.tick(config, binding, quiet_seconds=quiet_seconds, intent_routing=True)
    else:
        weixin_confirmations.process(config, binding, intent_routing=False)
        weixin_batches.advance(config, binding, quiet_seconds=quiet_seconds)


def select_download(
    config: Config,
    binding: Binding,
    reserve: Callable[[sqlite3.Connection, str], bool] | None,
    *,
    after_row: int = 0,
    eligible: Callable[[sqlite3.Connection, str], bool] | None = None,
) -> tuple[dict[str, Any], str, Any] | None:
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        selected = None
        for row in db.execute(
            "SELECT * FROM events WHERE account_id=? AND state='incomplete' "
            "AND rowid>? ORDER BY rowid",
            (binding.account_id, after_row),
        ):
            if eligible is not None and not eligible(db, row["job_id"]):
                continue
            if db.execute(
                "SELECT 1 FROM wx_batch_members m JOIN wx_batches b ON b.id=m.batch_id "
                "WHERE m.account_id=? AND m.event_id=? AND b.state='cancelled'",
                (binding.account_id, row["event_id"]),
            ).fetchone():
                continue
            event, _, _, state = recovery.load(db, binding, row["job_id"])
            if state != "incomplete":
                continue
            recovery.ensure_active(db, binding, row["event_id"])
            files = json.loads(event["raw"])["fiber_feishu"]["files"]
            _, media, required, _ = recovery.load(db, binding, row["job_id"])
            for key in sorted(required - media.keys()):
                if not db.execute(
                    "SELECT 1 FROM fiber_feishu_downloads WHERE job_id=? AND item_key=?",
                    (row["job_id"], key),
                ).fetchone():
                    selected = (dict(row), key, files[key])
                    break
            if selected:
                break
        if selected is None:
            return None
        row, key, file = selected
        if reserve is not None and not reserve(db, row["job_id"] + ":" + key):
            return None
        db.execute("INSERT INTO fiber_feishu_downloads VALUES(?,?,'running')", (row["job_id"], key))
        return row, key, file


def settle_download(config: Config, binding: Binding, job: str, key: str, state: str) -> None:
    with weixin_inbox.connection(config, binding) as db, database.transaction(db):
        db.execute(
            "UPDATE fiber_feishu_downloads SET state=? WHERE job_id=? AND item_key=?",
            (state, job, key),
        )


async def download_one(
    config: Config,
    binding: Binding,
    adapter: Any,
    *,
    reserve: Callable[[sqlite3.Connection, str], bool] | None = None,
    after_row: int = 0,
    eligible: Callable[[sqlite3.Connection, str], bool] | None = None,
    guard: Callable[[], None] | None = None,
) -> bool:
    """Reserve journal/quota atomically before SDK I/O; failures stay consumed on restart."""
    selected = await feishu_sync.run(
        select_download, config, binding, reserve, after_row=after_row, eligible=eligible
    )
    if selected is None:
        return False
    row, key, file = selected
    try:
        request = adapter._build_message_resource_request(
            message_id=row["event_id"], file_key=file["key"], resource_type=file["resource"]
        )
        response = await adapter._run_blocking(adapter._client.im.v1.message_resource.get, request)
        if response is None or not response.success() or response.file is None:
            raise InvalidInput("Feishu resource unavailable")

        def read() -> Any:
            try:
                return response.file.read(MAX_BYTES + 1)
            finally:
                response.file.close()

        data = await feishu_sync.run(read)
        if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
            raise InvalidInput("invalid Feishu resource length")
        await feishu_sync.run(
            recovery.complete,
            config,
            binding,
            row["job_id"],
            row["raw_sha256"],
            {key: data},
            guard=guard,
        )
    except Exception:
        await feishu_sync.run(settle_download, config, binding, row["job_id"], key, "failed")
        raise
    await feishu_sync.run(settle_download, config, binding, row["job_id"], key, "complete")
    return True
