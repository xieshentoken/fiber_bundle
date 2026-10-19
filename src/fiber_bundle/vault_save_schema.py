"""Additive schema 29: draft save policies and short-lived metadata previews."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE vault_save_policies (
      vault_id TEXT PRIMARY KEY REFERENCES vaults(id),
      revision INTEGER NOT NULL CHECK(revision>0),
      value TEXT NOT NULL CHECK(json_valid(value)),
      state TEXT NOT NULL CHECK(state IN ('draft')),
      updated_at REAL NOT NULL
    ) STRICT""")
    db.execute("""CREATE TABLE vault_directory_previews (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK(kind IN ('discovery','save_policy')),
      payload TEXT NOT NULL CHECK(json_valid(payload)),
      expires_at REAL NOT NULL
    ) STRICT""")
