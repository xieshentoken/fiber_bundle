"""Additive directory catalog; legacy Vault identities and grants are preserved."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    for statement in (
        "ALTER TABLE vaults ADD COLUMN absolute_root TEXT",
        "ALTER TABLE vaults ADD COLUMN root_kind TEXT NOT NULL DEFAULT 'legacy' "
        "CHECK(root_kind IN ('legacy','external','managed'))",
        "ALTER TABLE vaults ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0 "
        "CHECK(read_only IN (0,1))",
        "ALTER TABLE vaults ADD COLUMN attachment_state TEXT NOT NULL DEFAULT 'attached' "
        "CHECK(attachment_state IN ('attached','detached','trash_pending','trashed','reconcile'))",
        "ALTER TABLE vaults ADD COLUMN scope_json TEXT NOT NULL DEFAULT "
        '\'{"include":[""],"exclude":[]}\'',
        "CREATE TABLE vault_admissions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
        "expires_at REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0 "
        "CHECK(consumed IN (0,1))) STRICT",
        "CREATE TABLE vault_operations (id TEXT PRIMARY KEY, vault_id TEXT REFERENCES vaults(id), "
        "kind TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL) STRICT",
    ):
        db.execute(statement)
