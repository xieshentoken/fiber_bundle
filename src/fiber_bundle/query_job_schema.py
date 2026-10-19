"""Schema 16: durable, finite, explicitly launched model jobs."""

SCHEMA_V16 = """
CREATE TABLE query_jobs (
 id TEXT PRIMARY KEY,
 client_id TEXT NOT NULL REFERENCES clients(id),
 role TEXT NOT NULL CHECK(role IN ('local','cloud')),
 task_id TEXT NOT NULL,
 routing_digest TEXT NOT NULL,
 cloud_edit INTEGER NOT NULL CHECK(cloud_edit IN (0,1)),
 state TEXT NOT NULL CHECK(
 state IN ('reserved','running','succeeded','failed','cancelled','interrupted')),
 cloud_run_id TEXT REFERENCES cloud_answer_tasks(run_id),
 error_code TEXT,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(role,task_id)
) STRICT;
"""
