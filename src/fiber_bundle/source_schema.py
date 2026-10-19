"""Schema 20 extends explicit document formats and adds immutable GUI source bindings."""

SCHEMA_V20 = """
CREATE TABLE document_profiles_v20 (
 run_id TEXT PRIMARY KEY REFERENCES document_tasks(run_id),
 format TEXT NOT NULL CHECK(format IN ('pdf','docx','xlsx','pptx','image','chat','text','archive')),
 profile TEXT NOT NULL CHECK(profile IN ('native','ocr')),
 runtime_json TEXT,
 CHECK((profile='native' AND runtime_json IS NULL) OR (profile='ocr' AND runtime_json IS NOT NULL))
) STRICT;
INSERT INTO document_profiles_v20 SELECT * FROM document_profiles;
DROP TABLE document_profiles;
ALTER TABLE document_profiles_v20 RENAME TO document_profiles;
CREATE TABLE IF NOT EXISTS source_task_bindings (
 run_id TEXT PRIMARY KEY REFERENCES execution_runs(id),
 client_id TEXT NOT NULL REFERENCES clients(id),vault_id TEXT NOT NULL REFERENCES vaults(id),
 parent_receipt_id TEXT NOT NULL REFERENCES receipts(job_id),
 parent_version_id TEXT NOT NULL REFERENCES source_versions(id),
 parent_asset_id TEXT NOT NULL REFERENCES assets(id),
 parent_sha256 TEXT NOT NULL,batch_id TEXT,account_id TEXT,
 launch_state TEXT NOT NULL DEFAULT 'prepared'
 CHECK(launch_state IN ('prepared','launched','unknown')),
 submission_id TEXT NOT NULL,request_json TEXT NOT NULL,
 UNIQUE(client_id,submission_id)
) STRICT;
CREATE TABLE IF NOT EXISTS article_reviews (
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES article_tasks(run_id),
 manifest_sha256 TEXT NOT NULL,assessment_json TEXT NOT NULL,created_at REAL NOT NULL
) STRICT;
"""
