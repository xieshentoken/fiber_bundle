"""Schema35 permits unknown monetary reservations while preserving real token budgets."""

import sqlite3


def upgrade(db: sqlite3.Connection) -> None:
    # Caller owns the migration transaction with foreign keys disabled. Child
    # references keep the original table name; all historical rows remain exact.
    db.execute("""CREATE TABLE model_calls_v35 (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES execution_runs(id),
        model TEXT NOT NULL, price_revision TEXT NOT NULL, billing_day TEXT NOT NULL,
        currency TEXT NOT NULL CHECK(currency='USD'),
        reserved_nusd INTEGER NOT NULL CHECK(reserved_nusd>=0),
        charged_nusd INTEGER CHECK(charged_nusd>=0),
        input_tokens INTEGER CHECK(input_tokens>=0), output_tokens INTEGER CHECK(output_tokens>=0),
        state TEXT NOT NULL CHECK(state IN ('reserved','settled','uncertain')),
        created_at REAL NOT NULL
    ) STRICT""")
    db.execute("INSERT INTO model_calls_v35 SELECT * FROM model_calls")
    db.execute("DROP TABLE model_calls")
    db.execute("ALTER TABLE model_calls_v35 RENAME TO model_calls")
    db.execute("CREATE INDEX model_calls_day ON model_calls(billing_day,currency)")
