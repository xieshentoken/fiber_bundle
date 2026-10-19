"""Explicit bounded FIFO dispatcher for one client's local query intents."""

from typing import Any

from . import auth, database, local_query_execution
from .answer_worker import worker_lock
from .config import Config, InvalidInput, StorageError
from .local_query_tasks import local_policy
from .query_routing_config import QueryRoutingConfig


def dispatch(
    config: Config,
    policy: QueryRoutingConfig,
    client_id: str,
    *,
    max_tasks: int = 0,
    allow_inference: bool = False,
) -> dict[str, Any]:
    if type(max_tasks) is not int or not 0 <= max_tasks <= 100 or type(allow_inference) is not bool:
        raise InvalidInput("local_worker_limit_invalid")
    if not allow_inference or max_tasks == 0:
        return {"state": "disabled", "processed": 0, "results": []}
    local_policy(policy)
    results: list[dict[str, Any]] = []
    # Share the resource mutex with answer dispatch; no competing query batch is started.
    with worker_lock(config):
        for _ in range(max_tasks):
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    row = db.execute(
                        "SELECT id FROM local_query_tasks WHERE client_id=? AND config_digest=? "
                        "AND state='queued' AND NOT EXISTS "
                        "(SELECT 1 FROM local_query_calls c WHERE c.task_id=local_query_tasks.id) "
                        "ORDER BY created_at,id LIMIT 1",
                        (client_id, policy.digest),
                    ).fetchone()
                finally:
                    db.close()
            if row is None:
                return {"state": "idle", "processed": len(results), "results": results}
            try:
                result = local_query_execution.execute(config, policy, client_id, row["id"])
            except (auth.Denied, StorageError, OSError):
                return {
                    "state": "blocked",
                    "processed": len(results),
                    "results": results,
                    "task_id": row["id"],
                    "error_code": "local_dispatch_rejected",
                }
            results.append(result)
            if result["state"] != "succeeded" or not result["container_removed"]:
                return {"state": "stopped", "processed": len(results), "results": results}
    return {"state": "limit_reached", "processed": len(results), "results": results}
