"""Schema32: shared per-credential budgets and conservative usage reservations."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)


SCHEMA = """
CREATE TABLE model_budget_policies (
 credential_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,value TEXT NOT NULL,
 updated_at REAL NOT NULL
) STRICT;
CREATE TABLE model_credential_aliases (
 secret_file TEXT PRIMARY KEY,
 credential_id TEXT NOT NULL REFERENCES model_budget_policies(credential_id),
 original_id TEXT NOT NULL UNIQUE
) STRICT;
CREATE TABLE model_budget_reservations (
 id TEXT PRIMARY KEY,credential_id TEXT NOT NULL REFERENCES model_budget_policies(credential_id),
 period TEXT NOT NULL,unit TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>=0),
 charged INTEGER,state TEXT NOT NULL CHECK(state IN ('reserved','settled','uncertain')),
 created_at REAL NOT NULL
) STRICT;
CREATE INDEX model_budget_period ON model_budget_reservations(credential_id,period);
CREATE TABLE model_call_budget_bindings (
 call_id TEXT PRIMARY KEY REFERENCES model_calls(id),
 reservation_id TEXT NOT NULL REFERENCES model_budget_reservations(id)
) STRICT;
CREATE TABLE pi_processing_runs (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),kind TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,skill_sha256 TEXT NOT NULL,input_json TEXT NOT NULL
) STRICT;
"""
