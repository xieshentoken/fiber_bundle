"""Schema 15: explicit pre-call backup choices and refusals, without secrets."""

SCHEMA_V15 = """
CREATE TABLE cloud_answer_selections (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES cloud_answer_tasks(run_id),
 client_id TEXT NOT NULL REFERENCES clients(id),
 selection_id TEXT NOT NULL,
 routing_digest TEXT NOT NULL,
 backup_index INTEGER NOT NULL CHECK(backup_index BETWEEN 1 AND 4),
 previous_digest TEXT NOT NULL,
 selected_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('selected','refused')),
 reason TEXT NOT NULL,
 approval_kind TEXT NOT NULL CHECK(approval_kind='trusted_host_explicit'),
 created_at REAL NOT NULL,
 UNIQUE(run_id,selection_id)
) STRICT;
CREATE UNIQUE INDEX cloud_answer_selected_once ON cloud_answer_selections(run_id)
 WHERE state='selected';
"""
