"""Additive article publication and per-submission provenance ledger."""

SCHEMA_V5 = """
CREATE TABLE article_publications (
 id TEXT PRIMARY KEY, vault_id TEXT NOT NULL REFERENCES vaults(id),
 source_url TEXT NOT NULL, content_digest TEXT NOT NULL,
 vault_dev INTEGER NOT NULL, vault_ino INTEGER NOT NULL,
 version_id TEXT NOT NULL REFERENCES source_versions(id),
 relative_path TEXT NOT NULL, manifest_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('preparing','ready','published')),
 staging_path TEXT, error_code TEXT,
 UNIQUE(vault_id,source_url,content_digest), UNIQUE(vault_id,relative_path)
) STRICT;
CREATE TABLE article_publication_runs (
 run_id TEXT PRIMARY KEY REFERENCES article_tasks(run_id),
 publication_id TEXT NOT NULL REFERENCES article_publications(id),
 archive_receipt_id TEXT NOT NULL REFERENCES receipts(job_id)
) STRICT;
"""
