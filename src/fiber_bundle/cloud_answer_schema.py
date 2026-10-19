"""Schema 14: editing intents bound to an owned, validated local result."""

SCHEMA_V14 = """
CREATE TABLE cloud_answer_tasks (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 client_id TEXT NOT NULL REFERENCES clients(id),
 submission_id TEXT NOT NULL,
 local_task_id TEXT NOT NULL REFERENCES local_query_tasks(id),
 routing_digest TEXT NOT NULL,
 local_result_sha256 TEXT NOT NULL,
 result_text TEXT,
 UNIQUE(client_id,submission_id)
) STRICT;
"""
