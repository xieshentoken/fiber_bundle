"""Additive receipt-bound answer task persistence."""

SCHEMA_V6 = """
CREATE TABLE answer_tasks (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 receipt_id TEXT NOT NULL REFERENCES receipts(job_id),
 request_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 result_json TEXT
) STRICT;
"""
