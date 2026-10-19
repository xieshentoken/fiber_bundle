"""Additive schema 13: local answer intents and one durable non-monetary attempt."""

SCHEMA_V13 = """
CREATE TABLE local_query_tasks (
 id TEXT PRIMARY KEY,
 client_id TEXT NOT NULL REFERENCES clients(id), submission_id TEXT NOT NULL,
 vault_id TEXT NOT NULL REFERENCES vaults(id),
 version_id TEXT REFERENCES source_versions(id),
 binding_kind TEXT NOT NULL CHECK(binding_kind IN ('source','notes')),
 receipt_id TEXT REFERENCES receipts(job_id),
 input_kind TEXT NOT NULL CHECK(input_kind IN ('receipt','notes')),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
 roots_json TEXT NOT NULL CHECK(json_valid(roots_json)),
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 config_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','succeeded','failed','cancelled','interrupted')),
 error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(client_id,submission_id),
 CHECK((binding_kind='source' AND version_id IS NOT NULL AND
        input_kind='receipt' AND receipt_id IS NOT NULL) OR
       (binding_kind='notes' AND version_id IS NULL AND
        input_kind='notes' AND receipt_id IS NULL)),
 CHECK(state!='succeeded' OR result_json IS NOT NULL)
) STRICT;
CREATE TABLE local_query_calls (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES local_query_tasks(id),
 config_digest TEXT NOT NULL, billing_day TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','settled','uncertain')),
 input_tokens INTEGER CHECK(input_tokens>=0), output_tokens INTEGER CHECK(output_tokens>0),
 created_at REAL NOT NULL, completed_at REAL,
 CHECK((state='settled' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL
        AND completed_at IS NOT NULL) OR
       (state!='settled' AND input_tokens IS NULL AND output_tokens IS NULL))
) STRICT;
CREATE INDEX local_query_calls_day ON local_query_calls(billing_day);
"""
