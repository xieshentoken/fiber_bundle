"""Schema 31: explicit automation policies, durable jobs and genuine system review credentials."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
    # Existing human assessments remain unchanged. The two credential types are exclusive.
    db.execute("""CREATE TABLE document_publications_uv (
        run_id TEXT PRIMARY KEY REFERENCES document_tasks(run_id),
        review_id TEXT REFERENCES document_reviews(id),
        source_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE, staging_path TEXT NOT NULL UNIQUE,
        manifest_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('prepared','publishing','published','conflict')),
        created_at REAL NOT NULL,
        system_review_id TEXT REFERENCES automatic_reviews(id),
        CHECK((review_id IS NOT NULL AND system_review_id IS NULL) OR
              (review_id IS NULL AND system_review_id IS NOT NULL))
    ) STRICT""")
    db.execute("INSERT INTO document_publications_uv SELECT *,NULL FROM document_publications")
    db.execute("DROP TABLE document_publications")
    db.execute("ALTER TABLE document_publications_uv RENAME TO document_publications")

    db.execute("""CREATE TABLE media_publications_uv (
        job_id TEXT PRIMARY KEY REFERENCES media_processing(job_id),
        review_id TEXT REFERENCES media_reviews(id), relative_path TEXT NOT NULL,
        staging_path TEXT NOT NULL, manifest_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('prepared','publishing','published','conflict')),
        created_at REAL NOT NULL, system_review_id TEXT REFERENCES automatic_reviews(id),
        CHECK((review_id IS NOT NULL AND system_review_id IS NULL) OR
              (review_id IS NULL AND system_review_id IS NOT NULL))
    ) STRICT""")
    db.execute("INSERT INTO media_publications_uv SELECT *,NULL FROM media_publications")
    db.execute("DROP TABLE media_publications")
    db.execute("ALTER TABLE media_publications_uv RENAME TO media_publications")


SCHEMA = """
CREATE TABLE automatic_policies (
 vault_id TEXT PRIMARY KEY REFERENCES vaults(id), revision INTEGER NOT NULL CHECK(revision>0),
 value TEXT NOT NULL CHECK(json_valid(value)), updated_at REAL NOT NULL
) STRICT;
CREATE TABLE automatic_jobs (
 id TEXT PRIMARY KEY, vault_id TEXT NOT NULL REFERENCES vaults(id),
 client_id TEXT NOT NULL REFERENCES clients(id), kind TEXT NOT NULL,
 source_id TEXT NOT NULL, policy_revision INTEGER NOT NULL,
 phase TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','claimed','waiting','reviewed','complete','needs_review','cancelled','reconcile')),
 value TEXT NOT NULL CHECK(json_valid(value)), reason TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(kind,source_id)
) STRICT;
CREATE TABLE automatic_reviews (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES automatic_jobs(id),
 actor TEXT NOT NULL CHECK(actor IN ('system_rules','system_model')),
 value TEXT NOT NULL CHECK(json_valid(value)), created_at REAL NOT NULL
) STRICT;
CREATE INDEX automatic_jobs_queue ON automatic_jobs(state,updated_at);
"""
