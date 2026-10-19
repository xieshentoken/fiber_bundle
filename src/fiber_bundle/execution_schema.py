"""Additive M3 execution and conservative cost ledger schema."""

SCHEMA_V3 = """
CREATE TABLE execution_runs (
 id TEXT PRIMARY KEY,
 client_id TEXT NOT NULL REFERENCES clients(id),
 vault_id TEXT NOT NULL REFERENCES vaults(id),
 version_id TEXT NOT NULL REFERENCES source_versions(id),
 config_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','waiting_budget','succeeded','failed','cancelled','interrupted')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 task_limit_nusd INTEGER NOT NULL CHECK(task_limit_nusd>0),
 currency TEXT NOT NULL CHECK(currency='USD'),
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 error_code TEXT
) STRICT;
CREATE TABLE model_calls (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES execution_runs(id),
 model TEXT NOT NULL, price_revision TEXT NOT NULL, billing_day TEXT NOT NULL,
 currency TEXT NOT NULL CHECK(currency='USD'),
 reserved_nusd INTEGER NOT NULL CHECK(reserved_nusd>0),
 charged_nusd INTEGER CHECK(charged_nusd>=0),
 input_tokens INTEGER CHECK(input_tokens>=0), output_tokens INTEGER CHECK(output_tokens>=0),
 state TEXT NOT NULL CHECK(state IN ('reserved','settled','uncertain')),
 created_at REAL NOT NULL
) STRICT;
CREATE INDEX model_calls_day ON model_calls(billing_day,currency);
"""
