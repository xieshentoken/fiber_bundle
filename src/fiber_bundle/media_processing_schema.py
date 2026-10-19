"""Additive single-attempt processing, administrator reviews, and publication records."""

SCHEMA_V12 = """
CREATE TABLE media_processing (
 job_id TEXT PRIMARY KEY REFERENCES media_tasks(id),
 plan_json TEXT NOT NULL, state TEXT NOT NULL
 CHECK(state IN ('queued','running','complete','failed','cancelled','interrupted')),
 result_sha256 TEXT, error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
) STRICT;
CREATE TABLE media_reviews (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES media_processing(job_id),
 source_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL, glossary_revision INTEGER NOT NULL,
 assessment_json TEXT NOT NULL, created_at REAL NOT NULL
) STRICT;
CREATE TABLE media_publications (
 job_id TEXT PRIMARY KEY REFERENCES media_processing(job_id),
 review_id TEXT NOT NULL REFERENCES media_reviews(id),
 relative_path TEXT NOT NULL, staging_path TEXT NOT NULL, manifest_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','publishing','published','conflict')),
 created_at REAL NOT NULL
) STRICT;
"""

SCHEMA_V12 += """
CREATE TABLE media_link_tasks (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
 vault_id TEXT NOT NULL REFERENCES vaults(id), url TEXT NOT NULL, mode TEXT NOT NULL,
 declared_duration_ms INTEGER NOT NULL, image TEXT NOT NULL, keep_media INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','complete','blocked','failed','cancelled','interrupted')),
 job_id TEXT REFERENCES media_tasks(id), binding_json TEXT, error_code TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
) STRICT;
CREATE TABLE media_cleanup (
 job_id TEXT PRIMARY KEY REFERENCES media_tasks(id), manifest_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','deleting','complete')),
 created_at REAL NOT NULL
) STRICT;
"""
