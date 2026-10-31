"""Choose and back off local index work without starting extraction or organization."""

import time
from typing import Any

from .. import database, note_search_jobs
from ..config import Config
from . import problems, store


def next_task(config: Config, live: set[str], now: float) -> tuple[str, str] | None:
    with store.connection(config) as side:
        if side is None:
            return None
        candidates = side.execute(
            "SELECT * FROM libraries l WHERE baseline=1 AND index_after<=? AND "
            "(indexed<>generation OR state='index_pending' OR EXISTS "
            "(SELECT 1 FROM issues i WHERE i.vault=l.id AND next_check<=?)) AND NOT EXISTS "
            "(SELECT 1 FROM pending p WHERE p.vault=l.id AND attempts=0) ORDER BY index_after,id",
            (now, now),
        ).fetchall()
    with database.connect(config.state_root, readonly=True) as main:
        for row in candidates:
            if row["id"] not in live:
                continue
            client = main.execute(
                "SELECT c.id FROM clients c JOIN client_vault_grants g ON g.client_id=c.id "
                "JOIN vaults v ON v.id=g.vault_id WHERE c.active=1 AND g.can_query=1 "
                "AND v.status='active' AND v.attachment_state='attached' AND v.id=? "
                "ORDER BY c.id LIMIT 1",
                (row["id"],),
            ).fetchone()
            if client:
                return row["id"], client[0]
            finished(config, row["id"], failed=True, now=now)
    return None


def finished(
    config: Config,
    identity: str,
    *,
    failed: bool,
    now: float | None = None,
    error: BaseException | None = None,
) -> None:
    now = time.time() if now is None else now
    with store.connection(config, write=True) as side:
        assert side is not None
        row = side.execute("SELECT * FROM libraries WHERE id=?", (identity,)).fetchone()
        if row is None:
            return
        delay = store.DELAYS[min(row["index_attempts"], len(store.DELAYS) - 1)] if failed else 1
        side.execute(
            "UPDATE libraries SET index_after=?,index_attempts=? WHERE id=?",
            (now + delay, row["index_attempts"] + 1 if failed else 0, identity),
        )
        if failed:
            side.execute(
                "UPDATE libraries SET state='waiting',error=?,retries=retries+1 WHERE id=?",
                (problems.reason(error) if error else "vault_sync_index_waiting", identity),
            )

    if failed:
        with database.locked(config):
            note_search_jobs.record(config, identity, "waiting", reason="index_update_failed")


def dirty(config: Config, identity: str, paths: list[str]) -> bool:
    """Own committed writes use the same durable queue, not a second full scan."""
    row: dict[str, Any] | None = store.library(config, identity)
    if row is None:
        return False
    store.enqueue(config, identity, [(p, 1) for p in paths], row["cursor"])
    return True
