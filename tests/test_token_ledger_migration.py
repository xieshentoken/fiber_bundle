"""Schema34 monetary history and all child references survive the token-ledger migration."""

import sqlite3

from fiber_bundle import database, execution_ledger, model_budgets

pytest_plugins = ["test_execution"]


def test_migration_preserves_settled_money_tokens_and_foreign_keys(config, bound, policy):
    run, _, _ = bound
    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic existing budget", limit=500000),
            ),
        )
        execution_ledger.start(config, db, run, policy)
        call = execution_ledger.reserve(config, db, run, policy)
        execution_ledger.settle(db, call, policy, {"prompt_tokens": 20, "completion_tokens": 10})
        db.execute("PRAGMA foreign_keys=OFF")
        ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='model_calls'").fetchone()[0]
        with database.transaction(db):
            db.execute(ddl.replace("model_calls", "model_calls_v34", 1).replace(">=0", ">0", 1))
            db.execute("INSERT INTO model_calls_v34 SELECT * FROM model_calls")
            db.execute("DROP TABLE model_calls")
            db.execute("ALTER TABLE model_calls_v34 RENAME TO model_calls")
            db.execute("CREATE INDEX model_calls_day ON model_calls(billing_day,currency)")
            db.execute("PRAGMA user_version=34")
        rows = {
            r[0]: [tuple(row) for row in db.execute('SELECT * FROM "' + r[0] + '"')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    backup = database.migrate(config)
    assert backup is not None
    with database.connect(config.state_root) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 35
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table, original in rows.items():
            assert [tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')] == original
        assert (
            db.execute("SELECT charged_nusd FROM model_calls WHERE id=?", (call,)).fetchone()[0] > 0
        )
        assert model_budgets.inspect(db)[0]["used"] == 30
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 34
    assert database.migrate(config) is None
