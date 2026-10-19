"""Apply only files owned by this connection; ordinary profile/gateway remains independent."""

import sqlite3
import sys
from pathlib import Path
from typing import Any

from ..config import Config, make_directory
from ..inputs import canonical
from . import files, installation, models, store


def exported(config: Config, row: sqlite3.Row) -> dict[str, Any]:
    transport = files.folder(config, row["id"]) / "transport.json"
    command = {
        "command": sys.executable,
        "env": {
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "args": [
            "-m",
            "fiber_bundle.agent_connections.cli",
            "mcp",
            "--connection-file",
            str(transport),
        ],
    }
    return {
        "schema": "fiber-agent-export/v1",
        "contains_secrets": False,
        "mcpServers": {"fiber_bundle": command},
        "managed_entry": {
            "command": sys.executable,
            "env": command["env"],
            "args": [
                "-m",
                "fiber_bundle.agent_connections.cli",
                "request",
                "--connection-file",
                str(transport),
            ],
        },
        "configuration_sha256": row["configuration_sha256"],
        "revision": row["revision"],
    }


def apply(
    config: Config, db: sqlite3.Connection, row: sqlite3.Row, endpoint: str
) -> dict[str, Any]:
    value = store.spec(row)
    # A corrupt/missing secret requires explicit rotation, never a duplicated new identity.
    files.credential(config, row["id"])
    applied = {
        "revision": row["revision"],
        "credential_generation": row["credential_generation"],
        "configuration_sha256": row["configuration_sha256"],
        "agent_type": value.agent_type,
    }
    if value.agent_type == "hermes":
        runtime = installation.current()
        models.resolve(config, value.model_configuration_id)
        applied["hermes"] = runtime
        applied["model_configuration_sha256"] = models.binding_digest(
            config, value.model_configuration_id
        )
        home = files.folder(config, row["id"]) / "hermes-home"
        make_directory(home)
        from ..query_policy_store import write_private

        write_private(
            home / "config.yaml",
            canonical(
                {
                    "agent": {"api_max_retries": 1},
                    "compression": {"enabled": False},
                    "memory": {"memory_enabled": False, "user_profile_enabled": False},
                }
            ).encode(),
        )
        write_private(home / ".env", b"")
    files.put(
        config,
        row["id"],
        "transport.json",
        {
            "schema": "fiber-agent-transport/v1",
            "endpoint": endpoint,
            "credential_file": str(
                files.folder(config, row["id"], secret=True) / "credential.json"
            ),
            "connection_id": row["id"],
        },
    )
    files.put(config, row["id"], "export.json", exported(config, row))
    db.execute(
        "UPDATE agent_connections SET applied_json=?,probe_json=NULL,test_json=NULL WHERE id=?",
        (canonical(applied), row["id"]),
    )
    if row["state"] == "repair_required":
        db.execute(
            "UPDATE agent_connections SET state='paused',paused_epochs_json=? WHERE id=?",
            (canonical(store.epochs(db, row["client_id"])), row["id"]),
        )
    return {
        "state": "applied",
        "revision": row["revision"],
        "model_called": False,
        "capability": "tools_available_after_probe"
        if value.agent_type == "mcp"
        else "managed_entry_pending_test",
    }
