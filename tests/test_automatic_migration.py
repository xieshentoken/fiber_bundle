"""Explicit schema30 upgrade preserves populated human publication credentials and backup."""

import sqlite3

import pytest
from test_document_parts import chapters, processed
from test_media_jobs import media_scope as media_scope
from test_media_processing import complete
from test_media_publication import approved
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner

from fiber_bundle import database, document_publication, media_publication
from fiber_bundle.config import IntegrityError


@pytest.mark.parametrize("kind", ["document", "media"])
def test_populated_schema30_explicit_upgrade(config, owner, media_scope, kind):
    if kind == "document":
        db, rid, *_ = processed(config, owner, chapters())
        document_publication.publish_locked(config, db, rid)
    else:
        job = complete(config, media_scope)
        approved(config, media_scope, job)
        db = database.connect(config.state_root)
        media_publication.publish_locked(config, db, job)
    db.execute("PRAGMA foreign_keys=OFF")
    before = {}
    for table in ("document_publications", "media_publications"):
        before[table] = [tuple(r)[:-1] for r in db.execute("SELECT * FROM " + table)]
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        old_sql = sql[: sql.index("system_review_id")].rstrip().rstrip(",") + ") STRICT"
        old_sql = "CREATE TABLE old_" + table + old_sql[old_sql.index(" (") :]
        db.execute(old_sql)
        count = len(db.execute("PRAGMA table_info(old_" + table + ")").fetchall())
        db.executemany(
            "INSERT INTO old_" + table + " VALUES(" + ",".join("?" for _ in range(count)) + ")",
            before[table],
        )
        db.execute("DROP TABLE " + table)
        db.execute("ALTER TABLE old_" + table + " RENAME TO " + table)
    from schema_support import remove_automatic_schema

    remove_automatic_schema(db)
    db.execute("PRAGMA user_version=30")
    db.close()
    with pytest.raises(IntegrityError):
        database.connect(config.state_root)
    backup = database.migrate(config)
    assert backup
    with sqlite3.connect(backup) as prior:
        assert prior.execute("PRAGMA user_version").fetchone()[0] == 30
    with database.connect(config.state_root) as upgraded:
        for table, rows in before.items():
            assert [tuple(r) for r in upgraded.execute("SELECT * FROM " + table)] == [
                (*r, None) for r in rows
            ]
        assert upgraded.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not upgraded.execute("PRAGMA foreign_key_check").fetchall()
        assert upgraded.execute("SELECT count(*) FROM automatic_policies").fetchone()[0] == 0
    assert database.migrate(config) is None
