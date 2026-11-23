"""Reclaim obsolete FTS segments on the private build copy before capacity is exhausted."""

import sqlite3


def reclaim(index: sqlite3.Connection) -> None:
    pages = index.execute("PRAGMA page_count").fetchone()[0]
    free = index.execute("PRAGMA freelist_count").fetchone()[0]
    limit = index.execute("PRAGMA max_page_count").fetchone()[0]
    # FTS tombstones retain the previous generation's postings until a merge. Reclaim
    # only under page pressure; untouched live index bytes remain available to readers.
    if pages - free >= limit * 3 // 4:
        index.execute("INSERT INTO chunks(chunks) VALUES('optimize')")
