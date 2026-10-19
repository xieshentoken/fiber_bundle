"""Additive connection and operation ledger; existing grants remain authoritative."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE agent_connections (
      id TEXT PRIMARY KEY, client_id TEXT NOT NULL UNIQUE REFERENCES clients(id),
      configuration_json TEXT NOT NULL, configuration_sha256 TEXT NOT NULL,
      revision INTEGER NOT NULL, credential_generation INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('paused','enabled','revoked','repair_required')),
      paused_epochs_json TEXT NOT NULL, applied_json TEXT, probe_json TEXT, test_json TEXT,
      created_at REAL NOT NULL, updated_at REAL NOT NULL
    ) STRICT""")
    db.execute("""CREATE TABLE agent_connection_operations (
      id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES agent_connections(id),
      kind TEXT NOT NULL, request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
      revision INTEGER NOT NULL, credential_generation INTEGER NOT NULL,
      state TEXT NOT NULL
      CHECK(state IN ('queued','running','succeeded','failed','cancelled','reconcile')),
      cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
      payload_json TEXT NOT NULL, result_json TEXT, error_code TEXT,
      created_at REAL NOT NULL, updated_at REAL NOT NULL,
      UNIQUE(connection_id,request_id)
    ) STRICT""")
    db.execute(
        "CREATE UNIQUE INDEX agent_create_request ON "
        "agent_connection_operations(request_id) WHERE kind='create'"
    )

    db.execute("CREATE INDEX agent_operation_day ON agent_connection_operations(kind,created_at)")
    db.execute(
        "CREATE INDEX agent_operation_history ON "
        "agent_connection_operations(connection_id,created_at DESC,id DESC)"
    )
