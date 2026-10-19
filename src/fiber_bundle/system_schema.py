"""Additive schema 28: login policy and credential references for new usage records."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE system_settings (
      id INTEGER PRIMARY KEY CHECK(id=1),
      session_ttl INTEGER NOT NULL CHECK(session_ttl BETWEEN 1 AND 86400),
      revision INTEGER NOT NULL CHECK(revision>0)
    ) STRICT""")
    db.execute("""CREATE TABLE model_call_credentials (
      call_id TEXT PRIMARY KEY REFERENCES model_calls(id),
      credential_id TEXT NOT NULL, label TEXT NOT NULL, provider TEXT NOT NULL
    ) STRICT""")
    db.execute("CREATE INDEX model_call_credential_id ON model_call_credentials(credential_id)")
