"""Schema 25: source-bound full compiler tasks and immutable attempt history."""

import sqlite3

SCHEMA = """
CREATE TABLE source_expert_tasks (
 id TEXT PRIMARY KEY REFERENCES source_imports(id),
 request_sha256 TEXT NOT NULL,plan_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN (
 'prepared','running','paused','completed','failed','interrupted','cancelled')),
 current_attempt INTEGER NOT NULL DEFAULT 0 CHECK(current_attempt>=0),
 launch_state TEXT NOT NULL DEFAULT 'prepared' CHECK(launch_state IN (
 'prepared','launched','unknown')),
 next_command TEXT NOT NULL DEFAULT 'start' CHECK(next_command IN ('start','resume')),
 workpack_import_id TEXT REFERENCES source_imports(id),
 result_json TEXT, updated_at REAL NOT NULL
) STRICT;
CREATE TABLE source_expert_attempts (
 task_id TEXT NOT NULL REFERENCES source_expert_tasks(id),ordinal INTEGER NOT NULL,
 capture_id TEXT NOT NULL UNIQUE,workpack_import_id TEXT REFERENCES source_imports(id),
 state TEXT NOT NULL CHECK(state IN (
 'running','paused','completed','failed','interrupted','cancelled')),
 result_sha256 TEXT, created_at REAL NOT NULL,
 PRIMARY KEY(task_id,ordinal)
) STRICT;
"""


def upgrade(db: sqlite3.Connection) -> None:
    for statement in (SCHEMA + PUBLICATIONS).split(";"):
        if statement.strip():
            db.execute(statement)


PUBLICATIONS = """
CREATE TABLE source_expert_publications (
 task_id TEXT PRIMARY KEY REFERENCES source_expert_tasks(id),
 artifact_sha256 TEXT NOT NULL, assessment_json TEXT NOT NULL,
 relative_path TEXT NOT NULL,manifest_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','publishing','published','conflict')),
 created_at REAL NOT NULL
) STRICT;
"""
