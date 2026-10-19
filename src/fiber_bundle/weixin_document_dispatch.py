"""Explicitly configured Hermes document hook, independent of the existing receiver defaults."""

import asyncio
from typing import Any

from . import database, weixin_receiver
from . import document_creation_binding as binding
from . import document_creation_continuation as continuation
from . import document_creation_delivery as delivery
from . import document_creation_tasks as tasks
from . import document_creation_worker as worker
from .config import InvalidInput
from .document_creation_config import CreationExecutionConfig


def configure(
    adapter: Any,
    policy: CreationExecutionConfig,
    *,
    allow_inference: bool = False,
    allow_delivery: bool = False,
    upload_limit_bytes: int = 0,
    max_tasks: int = 1,
) -> None:
    if adapter._running or getattr(adapter, "_poll_task", None) is not None:
        raise InvalidInput("document_adapter_must_be_stopped")
    if type(allow_inference) is not bool or type(allow_delivery) is not bool:
        raise InvalidInput("document_dispatch_switch_invalid")
    if type(max_tasks) is not int or not 1 <= max_tasks <= 32:
        raise InvalidInput("document_worker_limit_invalid")
    if allow_delivery and (
        not allow_inference
        or type(upload_limit_bytes) is not int
        or not 1 <= upload_limit_bytes <= 8 * 1024 * 1024
    ):
        raise InvalidInput("document_dispatch_upload_policy_invalid")
    policy.validate()
    weixin_receiver.preflight(adapter)
    adapter._fiber_document_policy = policy
    adapter._fiber_document_configuration_sha256 = policy.sha256
    adapter._fiber_document_inference = allow_inference
    adapter._fiber_document_delivery = allow_delivery
    adapter._fiber_document_upload_limit = upload_limit_bytes
    adapter._fiber_document_max_tasks = max_tasks


async def dispatch(adapter: Any, task_id: str) -> dict[str, Any]:
    if getattr(adapter, "_fiber_document_inference", False) is not True:
        return {"state": "disabled", "processed": 0}
    config, owner = weixin_receiver.preflight(adapter)
    policy = getattr(adapter, "_fiber_document_policy", None)
    if (
        not isinstance(policy, CreationExecutionConfig)
        or policy.sha256 != adapter._fiber_document_configuration_sha256
    ):
        raise InvalidInput("document_dispatch_configuration_changed")
    sending = getattr(adapter, "_fiber_document_delivery", False) is True
    if sending and (
        not callable(getattr(adapter, "_fiber_send_file_once", None))
        or not callable(getattr(adapter, "_fiber_send_result_once", None))
    ):
        raise InvalidInput("document_guarded_sender_required")
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            tasks.plan(config, db, owner.client_id, task_id)
        finally:
            db.close()
    try:
        reports = await asyncio.to_thread(
            worker.drain,
            config,
            owner.client_id,
            task_id,
            policy,
            max_tasks=adapter._fiber_document_max_tasks,
            allow_provider=True,
        )
        delivered = []
        for report in reports:
            if not sending:
                break
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    row = db.execute(
                        "SELECT task_id FROM document_creation_runs WHERE run_id=? AND client_id=?",
                        (report["run_id"], owner.client_id),
                    ).fetchone()
                    if row is None:
                        raise InvalidInput("document_dispatch_owner_changed")
                    next_task = row[0]
                    binding.owned(db, owner.client_id, next_task)
                finally:
                    db.close()
            delivered.append(
                await delivery.deliver(
                    config,
                    owner,
                    next_task,
                    adapter._fiber_send_file_once,
                    adapter._fiber_send_result_once,
                    expected_artifact_sha256=report["draft_version"]["artifact"]["sha256"],
                    max_bytes=adapter._fiber_document_upload_limit,
                    allow_delivery=True,
                )
            )
        return {
            "state": "completed",
            "processed": len(reports),
            "results": reports,
            "file_deliveries": delivered,
        }
    except asyncio.CancelledError:
        # Stop admissions and subsequent send gates. A synchronous in-flight provider reply
        # can arrive late; its known fee still records under the existing stopped owner.
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                for value in reversed(continuation.versions(config, db, owner.client_id, task_id)):
                    tasks.cancel(
                        config,
                        db,
                        owner.client_id,
                        value["task_id"],
                        expected_revision=value["revision"],
                        expected_sha256=value["plan_sha256"],
                    )
            finally:
                db.close()
        raise
