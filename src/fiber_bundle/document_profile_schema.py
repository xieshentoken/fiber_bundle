"""Schema 24: explicit legacy DOC conversion; preserve existing profiles verbatim."""

import re
import sqlite3

from .config import IntegrityError


def upgrade(db: sqlite3.Connection) -> None:
    ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='document_profiles'").fetchone()[0]
    if "'native','ocr'" not in ddl or "'archive'" not in ddl or "'conversion'" in ddl:
        raise IntegrityError("document profile migration baseline")
    new = re.sub(
        r'CREATE TABLE "?document_profiles"?', "CREATE TABLE document_profiles_new", ddl, count=1
    )
    new = new.replace("'native','ocr'", "'native','ocr','conversion'")
    new = new.replace("'archive'", "'archive','doc'")
    new = new.replace(
        "(profile='ocr' AND runtime_json IS NOT NULL)",
        "(profile IN ('ocr','conversion') AND runtime_json IS NOT NULL)",
    )
    db.execute(new)
    db.execute("INSERT INTO document_profiles_new SELECT * FROM document_profiles")
    db.execute("DROP TABLE document_profiles")
    db.execute("ALTER TABLE document_profiles_new RENAME TO document_profiles")
