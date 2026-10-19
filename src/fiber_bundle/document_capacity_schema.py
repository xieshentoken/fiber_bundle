"""Schema 23: larger native PDF tasks, retaining every historical binding and value."""

import sqlite3

from .config import IntegrityError


def upgrade(db: sqlite3.Connection) -> None:
    ddl = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='document_tasks'"
    ).fetchone()[0]
    if "source_bytes<=8388608" not in ddl or "page_count<=100" not in ddl:
        raise IntegrityError("document capacity migration baseline")
    columns = [row[1] for row in db.execute("PRAGMA table_info(document_tasks)")]
    names = ",".join(columns)
    new = ddl.replace("CREATE TABLE document_tasks", "CREATE TABLE document_tasks_new", 1)
    new = new.replace("source_bytes<=8388608", "source_bytes<=16777216").replace(
        "page_count<=100", "page_count<=300"
    )
    db.execute(new)
    db.execute(f"INSERT INTO document_tasks_new ({names}) SELECT {names} FROM document_tasks")
    db.execute("DROP TABLE document_tasks")
    db.execute("ALTER TABLE document_tasks_new RENAME TO document_tasks")
