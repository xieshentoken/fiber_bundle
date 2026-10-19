"""Adopt one explicitly configured legacy Key's budget during a matching managed save."""

import hmac
import sqlite3
from pathlib import Path

from .. import model_budgets, query_policy_store
from ..config import Config, StorageError
from ..execution_config import read_secret
from ..usage_credentials import reference


def budget_identity(
    config: Config, db: sqlite3.Connection, connection: dict[str, str], key: str
) -> str | None:
    # No directory scan or unrelated Agent secret read. Only the two declared route files,
    # and only when their exact service/protocol matches the user's submitted connection.
    route = query_policy_store.legacy_current(config)
    if route is None:
        return None
    for value in (route.local_query, route.cloud_answer):
        if value is None or not value.secret_file:
            continue
        suffix = {
            "openai-completions": "/chat/completions",
            "openai-responses": "/responses",
            "anthropic-messages": "/messages",
        }[connection["protocol"]]
        if value.endpoint != connection["base_url"] + suffix:
            continue
        try:
            previous = read_secret(Path(value.secret_file))
        except (OSError, StorageError):
            continue
        if hmac.compare_digest(previous.encode(), key.encode()):
            return model_budgets.key(db, value.secret_file) or reference(value.secret_file)["id"]
    return None
