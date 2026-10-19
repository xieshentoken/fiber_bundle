"""Explicit versioned migration of the Feishu command journal CHECK contract."""

import sqlite3

STATES = (
    "'queued','needs_selection','dispatching','submitted','failed','wa"
    "iting_file','awaiting_download','bound','expired','cancelled','un"
    "known'"
)


def initialize(db: sqlite3.Connection) -> None:
    old = db.execute("SELECT sql FROM sqlite_master WHERE name='fiber_expert_commands'").fetchone()
    db.execute(
        "CREATE TABLE IF NOT EXISTS fiber_expert_command_schema(version IN"
        "TEGER PRIMARY KEY CHECK(version=2)) STRICT"
    )
    ddl = f"""CREATE TABLE fiber_expert_commands_uv (
      authorization TEXT,event_id TEXT,request_json TEXT,
      state TEXT CHECK(state IN ({STATES})), task_id TEXT,
      PRIMARY KEY(authorization,event_id)
    ) STRICT"""
    if old is None:
        db.execute(ddl.replace("fiber_expert_commands_uv", "fiber_expert_commands"))
    elif "waiting_file" not in old[0]:
        db.execute(ddl)
        db.execute("INSERT INTO fiber_expert_commands_uv SELECT * FROM fiber_expert_commands")
        db.execute("DROP TABLE fiber_expert_commands")
        db.execute("ALTER TABLE fiber_expert_commands_uv RENAME TO fiber_expert_commands")
    db.execute("INSERT OR IGNORE INTO fiber_expert_command_schema VALUES(2)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS fiber_expert_control_events("
        "authorization TEXT NOT NULL,event_id TEXT NOT NULL,sha256 TEXT NOT NULL,"
        "PRIMARY KEY(authorization,event_id)) STRICT"
    )
