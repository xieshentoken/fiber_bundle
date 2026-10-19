"""Private pipe-owned SQLite reservations, isolated from raw backup file descriptors.

Closing an unrelated raw descriptor can release a process's POSIX SQLite locks.
This process opens database files only through SQLite and exits when its pipe closes.
"""

import json
import sqlite3
import stat
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from ..config import contained


def main() -> None:
    try:
        value = json.loads(sys.stdin.readline(65537))
        root = Path(value["root"])
        names = value["names"]
        if root.resolve(strict=True) != root or not 1 <= len(names) <= 20000:
            raise ValueError()
        with ExitStack() as stack:
            deadline = time.monotonic() + 2
            for name in names:
                path = contained(root, name)
                if not path.exists():
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError()
                db = sqlite3.connect(
                    path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None
                )
                stack.callback(db.close)
                result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if result and result[0]:
                    raise sqlite3.OperationalError()
                db.execute("BEGIN IMMEDIATE")
                # A writer may have committed between checkpoint and reservation.
                # Immutable main-file readers must never omit such WAL frames.
                wal = Path(str(path) + "-wal")
                if wal.exists() and wal.stat().st_size:
                    raise sqlite3.OperationalError()
                if time.monotonic() > deadline:
                    raise sqlite3.OperationalError()
            print('"ready"', flush=True)
            sys.stdin.buffer.read(1)  # EOF on parent exit, including interruption/crash.
    except Exception:
        print('"backup_database_busy"', flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
