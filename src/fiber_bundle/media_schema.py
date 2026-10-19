"""Offline media tasks are distinct from paid model execution and document profiles."""

SCHEMA_V10 = """
CREATE TABLE media_tasks (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
 vault_id TEXT NOT NULL REFERENCES vaults(id), binding_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','complete','failed','cancelled','interrupted')),
 result_sha256 TEXT, error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 CHECK(state!='complete' OR result_sha256 IS NOT NULL)
) STRICT;
"""
