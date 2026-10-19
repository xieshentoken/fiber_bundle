"""Schema34 adds model configuration without rewriting historical policy snapshots."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    for sql in SCHEMA.split(";"):
        if sql.strip():
            db.execute(sql)


SCHEMA = """
CREATE TABLE model_credentials (
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL, generation INTEGER NOT NULL,
 settings_json TEXT NOT NULL, fingerprint TEXT NOT NULL, secret_file TEXT NOT NULL,
 active INTEGER NOT NULL CHECK(active IN (0,1)), created_at REAL NOT NULL,
 UNIQUE(fingerprint)
) STRICT;
CREATE TABLE model_configurations (
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
 credential_id TEXT NOT NULL REFERENCES model_credentials(id),
 settings_json TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 verification_json TEXT, created_at REAL NOT NULL
) STRICT;
CREATE TABLE model_configuration_settings (
 key TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL
) STRICT;
CREATE TABLE model_configuration_calls (
 id TEXT PRIMARY KEY, configuration_id TEXT NOT NULL REFERENCES model_configurations(id),
 credential_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 state TEXT NOT NULL, policy_json TEXT NOT NULL, reservation_id TEXT NOT NULL,
 input_tokens INTEGER,output_tokens INTEGER,charged_nusd INTEGER,error_code TEXT,
 created_at REAL NOT NULL
) STRICT;
"""
