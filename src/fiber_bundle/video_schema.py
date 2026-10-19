"""Schema33: resumable long-video processing, separate from frozen legacy media tasks."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE video_tasks (
      id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
      vault_id TEXT NOT NULL REFERENCES vaults(id), import_id TEXT NOT NULL UNIQUE
      REFERENCES source_imports(id), state TEXT NOT NULL, policy_json TEXT NOT NULL,
      value TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    ) STRICT""")
