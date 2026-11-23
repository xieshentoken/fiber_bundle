"""Schema 36: expand login policy, preserving saved policy and all sessions."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE system_settings_v36 (
      id INTEGER PRIMARY KEY CHECK(id=1),
      session_ttl INTEGER NOT NULL CHECK(session_ttl BETWEEN 0 AND 2592000),
      revision INTEGER NOT NULL CHECK(revision>0)
    ) STRICT""")
    db.execute("INSERT INTO system_settings_v36 SELECT * FROM system_settings")
    db.execute("DROP TABLE system_settings")
    db.execute("ALTER TABLE system_settings_v36 RENAME TO system_settings")
