"""Schema 7 binding migration. Caller owns backup, transaction and FK connection policy."""

import sqlite3

EXECUTION_COLUMNS = (
    "id,client_id,vault_id,version_id,config_digest,state,attempts,task_limit_nusd,"
    "currency,created_at,updated_at,error_code"
)
ANSWER_COLUMNS = "run_id,receipt_id,request_json,snapshot_json,result_json"

SCHEMA_V7_RUNS = """
CREATE TABLE execution_runs_new (
 id TEXT PRIMARY KEY,
 client_id TEXT NOT NULL REFERENCES clients(id),
 vault_id TEXT NOT NULL REFERENCES vaults(id),
 version_id TEXT REFERENCES source_versions(id),
 config_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','waiting_budget','succeeded','failed','cancelled','interrupted')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 task_limit_nusd INTEGER NOT NULL CHECK(task_limit_nusd>0),
 currency TEXT NOT NULL CHECK(currency='USD'),
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 error_code TEXT,
 binding_kind TEXT NOT NULL DEFAULT 'source' CHECK(binding_kind IN ('source','notes')),
 CHECK((binding_kind='source' AND version_id IS NOT NULL) OR
       (binding_kind='notes' AND version_id IS NULL))
) STRICT
"""
SCHEMA_V7_ANSWERS = """
CREATE TABLE answer_tasks_new (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 receipt_id TEXT REFERENCES receipts(job_id),
 request_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, result_json TEXT,
 input_kind TEXT NOT NULL DEFAULT 'receipt' CHECK(input_kind IN ('receipt','notes')),
 CHECK((input_kind='receipt' AND receipt_id IS NOT NULL) OR
       (input_kind='notes' AND receipt_id IS NULL))
) STRICT
"""


def upgrade(connection: sqlite3.Connection) -> None:
    for table, ddl, columns in (
        ("execution_runs", SCHEMA_V7_RUNS, EXECUTION_COLUMNS),
        ("answer_tasks", SCHEMA_V7_ANSWERS, ANSWER_COLUMNS),
    ):
        connection.execute(ddl)
        connection.execute(f"INSERT INTO {table}_new ({columns}) SELECT {columns} FROM {table}")
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
