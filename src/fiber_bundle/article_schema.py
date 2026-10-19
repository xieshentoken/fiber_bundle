"""Additive M4 binding; existing execution and receipt rows retain their semantics."""

SCHEMA_V4 = """
CREATE TABLE article_tasks (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 receipt_id TEXT NOT NULL REFERENCES receipts(job_id),
 source_url TEXT NOT NULL,
 worker_image TEXT NOT NULL, worker_timeout INTEGER NOT NULL CHECK(worker_timeout>0),
 capture_task_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('queued','capturing','captured','failed','interrupted')),
 manifest_sha256 TEXT,
 image_count INTEGER CHECK(image_count>=0), markdown_bytes INTEGER CHECK(markdown_bytes>0),
 error_code TEXT,
 CHECK(state!='captured' OR
 (manifest_sha256 IS NOT NULL AND image_count IS NOT NULL AND markdown_bytes IS NOT NULL))
) STRICT;
"""
