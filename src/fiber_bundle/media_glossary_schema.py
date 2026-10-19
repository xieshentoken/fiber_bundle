"""Append-only task-local glossary revisions and explicit owner confirmations."""

SCHEMA_V11 = """
CREATE TABLE media_glossary_revisions (
 job_id TEXT NOT NULL REFERENCES media_tasks(id),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 100),
 snapshot_sha256 TEXT NOT NULL, raw_sha256 TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 raw_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(job_id,revision)
) STRICT;
CREATE TABLE media_glossary_confirmations (
 job_id TEXT NOT NULL, revision INTEGER NOT NULL, snapshot_sha256 TEXT NOT NULL,
 client_id TEXT NOT NULL REFERENCES clients(id),
 allow_empty INTEGER NOT NULL CHECK(allow_empty IN (0,1)),
 created_at REAL NOT NULL, PRIMARY KEY(job_id,revision),
 FOREIGN KEY(job_id,revision) REFERENCES media_glossary_revisions(job_id,revision)
) STRICT;
"""
