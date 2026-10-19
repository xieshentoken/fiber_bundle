"""Hermes candidate hands confirmed batches to the persisted local-query policy.

Queue only; host approval of each independent job remains in the management API.
The existing guarded result outbox delivers local answers with source checks.
"""

from typing import Any

from . import database, query_policy_store, weixin_task_bindings
from .config import Config, InvalidInput
from .weixin_events import Binding


def submit(config: Config, binding: Binding, batch_id: str) -> dict[str, Any]:
    with database.locked(config):
        route = query_policy_store.current(config)
        if route is None:
            raise InvalidInput("local_query_not_configured")
    return weixin_task_bindings.submit(config, binding, batch_id, route)
