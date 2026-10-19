"""Finite FIFO cloud editing batches for one client and frozen route."""

from typing import Any

from . import auth, cloud_answer_execution, database
from .answer_worker import worker_lock
from .config import Config, InvalidInput, StorageError
from .inputs import text
from .query_routing_config import QueryRoutingConfig


def dispatch(
    config: Config,
    route: QueryRoutingConfig,
    client_id: str,
    *,
    max_tasks: int = 0,
    allow_inference: bool = False,
) -> dict[str, Any]:
    if type(max_tasks) is not int or not 0 <= max_tasks <= 100 or type(allow_inference) is not bool:
        raise InvalidInput("cloud_worker_limit_invalid")
    if not allow_inference or max_tasks == 0:
        return {"state": "disabled", "processed": 0, "results": []}
    route.validate()
    policy = route.answer_policy()
    text(client_id, limit=128)
    results: list[dict[str, Any]] = []
    with worker_lock(config):
        for _ in range(max_tasks):
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    client = db.execute(
                        "SELECT active FROM clients WHERE id=?", (client_id,)
                    ).fetchone()
                    if client is None or not client[0]:
                        raise auth.Denied(401)
                    row = db.execute(
                        "SELECT r.id FROM execution_runs r "
                        "JOIN cloud_answer_tasks t ON t.run_id=r.id "
                        "WHERE r.client_id=? AND t.client_id=? AND t.routing_digest=? "
                        "AND (r.config_digest=? OR EXISTS (SELECT 1 FROM cloud_answer_selections s "
                        "WHERE s.run_id=r.id AND s.client_id=r.client_id AND s.state='selected' "
                        "AND s.routing_digest=t.routing_digest "
                        "AND s.selected_digest=r.config_digest)) "
                        "AND r.state='queued' AND r.attempts=0 "
                        "AND NOT EXISTS (SELECT 1 FROM model_calls c WHERE c.run_id=r.id) "
                        "ORDER BY r.created_at,r.id LIMIT 1",
                        (client_id, client_id, route.digest, policy.digest),
                    ).fetchone()
                finally:
                    db.close()
            if row is None:
                return {"state": "idle", "processed": len(results), "results": results}
            try:
                result = cloud_answer_execution.execute(
                    config, route, client_id, row["id"], allow_inference=True
                )
            except (auth.Denied, StorageError, OSError):
                return {
                    "state": "blocked",
                    "processed": len(results),
                    "results": results,
                    "run_id": row["id"],
                    "error_code": "cloud_dispatch_rejected",
                }
            results.append(result)
            if result["state"] != "succeeded":
                return {"state": "stopped", "processed": len(results), "results": results}
    return {"state": "limit_reached", "processed": len(results), "results": results}
