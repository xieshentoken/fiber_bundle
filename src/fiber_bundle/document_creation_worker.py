"""Explicit bounded document queue drain; one heavy document worker per state root."""

from collections.abc import Mapping
from typing import Any

from . import database, executor_container
from . import document_creation_binding as binding
from . import document_creation_continuation as continuation
from . import document_creation_runner as runner
from . import document_creation_tasks as tasks
from .config import Config, InvalidInput
from .document_creation_config import CreationExecutionConfig

WORKER_LOCK = "376b3acd-0ae3-4bb7-88b2-390b92322593"


def drain(
    config: Config,
    client_id: str,
    task_id: str,
    policy: CreationExecutionConfig,
    *,
    max_tasks: int = 1,
    allow_provider: bool = False,
    synthetic_drafts: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    if type(max_tasks) is not int or not 1 <= max_tasks <= 32:
        raise InvalidInput("document_worker_limit_invalid")
    if allow_provider is not True and synthetic_drafts is None:
        raise InvalidInput("document_worker_not_enabled")
    reports = []
    with executor_container.run_lock(config, WORKER_LOCK):
        for _ in range(max_tasks):
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    status = tasks.status(config, db, client_id, task_id)
                    existing = binding.get(db, task_id)
                    if existing is not None:
                        report = binding.report(db, existing)
                        if report["state"] != "succeeded":
                            raise InvalidInput("document_worker_existing_attempt_not_replayable")
                    else:
                        report = None
                finally:
                    db.close()
            if report is None:
                options = dict(
                    expected_revision=status["revision"],
                    expected_plan_sha256=status["plan_sha256"],
                    expected_configuration_sha256=policy.sha256,
                )
                if allow_provider:
                    report = runner.run_provider(
                        config, client_id, task_id, policy, allow_provider=True, **options
                    )
                else:
                    if synthetic_drafts is None or task_id not in synthetic_drafts:
                        raise InvalidInput("document_synthetic_reply_not_supplied")
                    report = runner.run_synthetic(
                        config,
                        client_id,
                        task_id,
                        policy,
                        synthetic_drafts[task_id],
                        allow_synthetic=True,
                        **options,
                    )
            reports.append(report)
            # A completed version is immutable. Only its separately frozen child can be admitted.
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    child = db.execute(
                        "SELECT child_task_id FROM document_creation_continuations "
                        "WHERE parent_task_id=?",
                        (task_id,),
                    ).fetchone()
                    if child is None:
                        break
                    result = continuation.release(config, db, client_id, task_id)
                    if result["state"] != "ready":
                        break
                    task_id = child[0]
                finally:
                    db.close()
    return reports
