"""Schema 22: resumable GUI uploads and explicitly model-free document runs."""

import sqlite3

from .answer_migration import EXECUTION_COLUMNS

SCHEMA = """
CREATE TABLE source_imports (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
 vault_id TEXT NOT NULL REFERENCES vaults(id), submission_id TEXT NOT NULL,
 request_json TEXT NOT NULL, plan_json TEXT NOT NULL, permission_epoch INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('uploading','archived','cancelled','interrupted')),
 received_bytes INTEGER NOT NULL DEFAULT 0 CHECK(received_bytes>=0),
 receipt_id TEXT REFERENCES receipts(job_id), asset_id TEXT REFERENCES assets(id),
 run_id TEXT UNIQUE REFERENCES execution_runs(id), created_at REAL NOT NULL,
 UNIQUE(client_id,submission_id)
) STRICT;
CREATE TABLE source_import_chunks (
 import_id TEXT NOT NULL REFERENCES source_imports(id), ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL CHECK(byte_count>0),
 PRIMARY KEY(import_id,ordinal)
) STRICT;
"""


def upgrade(db: sqlite3.Connection) -> None:
    # Keep every historical column/value and dependent FK. Caller owns FK-off transaction.
    ddl = """
CREATE TABLE execution_runs_new (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
 vault_id TEXT NOT NULL REFERENCES vaults(id), version_id TEXT REFERENCES source_versions(id),
 config_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','waiting_budget','succeeded','failed','cancelled','interrupted')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 task_limit_nusd INTEGER NOT NULL,
 currency TEXT NOT NULL CHECK(currency='USD'), created_at REAL NOT NULL, updated_at REAL NOT NULL,
 error_code TEXT,
 binding_kind TEXT NOT NULL DEFAULT 'source' CHECK(binding_kind IN ('source','notes','document')),
 CHECK((binding_kind='document' AND task_limit_nusd=0) OR
       (binding_kind IN ('source','notes') AND task_limit_nusd>0)),
 CHECK((binding_kind IN ('source','document') AND version_id IS NOT NULL) OR
       (binding_kind='notes' AND version_id IS NULL))
) STRICT
"""
    columns = EXECUTION_COLUMNS + ",binding_kind"
    db.execute(ddl)
    db.execute(f"INSERT INTO execution_runs_new ({columns}) SELECT {columns} FROM execution_runs")
    db.execute("DROP TABLE execution_runs")
    db.execute("ALTER TABLE execution_runs_new RENAME TO execution_runs")
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
