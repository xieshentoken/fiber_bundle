"""Schema 30: explicit activation and durable, versioned publication locations."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE vault_save_policies RENAME TO vault_save_policies_draft")
    db.execute("""CREATE TABLE vault_save_policies (
      vault_id TEXT PRIMARY KEY REFERENCES vaults(id), revision INTEGER NOT NULL CHECK(revision>0),
      value TEXT NOT NULL CHECK(json_valid(value)),
      state TEXT NOT NULL CHECK(state IN ('draft','active')), updated_at REAL NOT NULL
    ) STRICT""")
    db.execute("INSERT INTO vault_save_policies SELECT * FROM vault_save_policies_draft")
    db.execute("DROP TABLE vault_save_policies_draft")
    db.execute("""CREATE TABLE publication_locations (
      kind TEXT NOT NULL CHECK(kind IN ('article','document','media','intake','expert')),
      publication_id TEXT NOT NULL, vault_id TEXT NOT NULL REFERENCES vaults(id),
      entry_note_path TEXT NOT NULL, package_root TEXT,
      revision INTEGER NOT NULL CHECK(revision>0),
      state TEXT NOT NULL CHECK(state IN ('prepared','committing','published','reconcile')),
      value TEXT NOT NULL CHECK(json_valid(value)),
      PRIMARY KEY(kind,publication_id)
    ) STRICT""")
    db.execute("CREATE INDEX publication_locations_vault ON publication_locations(vault_id)")
