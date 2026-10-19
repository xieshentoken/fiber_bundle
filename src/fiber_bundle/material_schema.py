"""Additive format/profile, human review and publication ledger for M8 documents."""

SCHEMA_V9 = """
CREATE TABLE document_profiles (
 run_id TEXT PRIMARY KEY REFERENCES document_tasks(run_id),
 format TEXT NOT NULL CHECK(format IN ('pdf','docx','xlsx','pptx','image','chat')),
 profile TEXT NOT NULL CHECK(profile IN ('native','ocr')),
 runtime_json TEXT,
 CHECK((profile='native' AND runtime_json IS NULL) OR (profile='ocr' AND runtime_json IS NOT NULL))
) STRICT;
CREATE TABLE document_reviews (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES document_tasks(run_id),
 source_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL,
 assessment_json TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL
) STRICT;
CREATE TABLE document_publications (
 run_id TEXT PRIMARY KEY REFERENCES document_tasks(run_id),
 review_id TEXT NOT NULL REFERENCES document_reviews(id),
 source_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL,
 relative_path TEXT NOT NULL UNIQUE, staging_path TEXT NOT NULL UNIQUE,
 manifest_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','publishing','published','conflict')),
 created_at REAL NOT NULL
) STRICT;
"""
