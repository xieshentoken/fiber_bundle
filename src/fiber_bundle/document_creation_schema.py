"""Schema 17: fixed document intent and owner, sharing the existing fee ledger."""

SCHEMA_V17 = """
CREATE TABLE document_creation_runs (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 task_id TEXT NOT NULL UNIQUE,
 client_id TEXT NOT NULL REFERENCES clients(id),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 32),
 plan_sha256 TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 configuration_sha256 TEXT NOT NULL,
 configuration_json TEXT NOT NULL CHECK(json_valid(configuration_json)),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 request_sha256 TEXT NOT NULL,
 owner_id TEXT NOT NULL UNIQUE
) STRICT;
"""

SCHEMA_V18 = """
CREATE TABLE document_creation_sessions (
 run_id TEXT PRIMARY KEY REFERENCES document_creation_runs(run_id),
 profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
 profile_sha256 TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode='synthetic')
) STRICT;
CREATE TABLE document_creation_calls (
 call_id TEXT PRIMARY KEY REFERENCES model_calls(id),
 run_id TEXT NOT NULL REFERENCES document_creation_sessions(run_id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 2),
 request_json TEXT NOT NULL CHECK(json_valid(request_json)), request_sha256 TEXT NOT NULL,
 response_json TEXT CHECK(response_json IS NULL OR json_valid(response_json)),
 response_sha256 TEXT,
 CHECK((response_json IS NULL) = (response_sha256 IS NULL)), UNIQUE(run_id,ordinal)
) STRICT;
CREATE TABLE document_creation_artifacts (
 run_id TEXT PRIMARY KEY REFERENCES document_creation_sessions(run_id),
 version INTEGER NOT NULL CHECK(version=1),
 relative_path TEXT NOT NULL UNIQUE,
 artifact_json TEXT NOT NULL CHECK(json_valid(artifact_json)),
 artifact_sha256 TEXT NOT NULL, created_at REAL NOT NULL
) STRICT;
"""

# Existing qualification records remain synthetic; provider dispatch is an explicit candidate mode.
SCHEMA_V19 = """
CREATE TABLE document_creation_sessions_v19 (
 run_id TEXT PRIMARY KEY REFERENCES document_creation_runs(run_id),
 profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
 profile_sha256 TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('synthetic','provider'))
) STRICT;
INSERT INTO document_creation_sessions_v19 SELECT * FROM document_creation_sessions;
DROP TABLE document_creation_sessions;
ALTER TABLE document_creation_sessions_v19 RENAME TO document_creation_sessions;
CREATE TABLE document_creation_continuations (
 parent_task_id TEXT PRIMARY KEY REFERENCES document_creation_runs(task_id),
 child_task_id TEXT NOT NULL UNIQUE, client_id TEXT NOT NULL REFERENCES clients(id),
 intent_json TEXT NOT NULL CHECK(json_valid(intent_json)), intent_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','ready','cancelled')), created_at REAL NOT NULL
) STRICT;
CREATE TABLE document_creation_deliveries (
 run_id TEXT PRIMARY KEY REFERENCES document_creation_artifacts(run_id),
 intent_json TEXT NOT NULL CHECK(json_valid(intent_json)),intent_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','failed','unknown')),
 notice_state TEXT NOT NULL CHECK(notice_state IN (
 'pending','sending','sent','unavailable','not_needed')),
 created_at REAL NOT NULL
) STRICT;
CREATE TABLE document_creation_saves (
 run_id TEXT PRIMARY KEY REFERENCES document_creation_artifacts(run_id),
 vault_id TEXT NOT NULL REFERENCES vaults(id),
 intent_json TEXT NOT NULL CHECK(json_valid(intent_json)),intent_sha256 TEXT NOT NULL,
 approval_json TEXT CHECK(approval_json IS NULL OR json_valid(approval_json)),
 source_version_id TEXT REFERENCES source_versions(id),
 state TEXT NOT NULL CHECK(state IN ('prepared','publishing','published','conflict')),
 created_at REAL NOT NULL
) STRICT;
"""
