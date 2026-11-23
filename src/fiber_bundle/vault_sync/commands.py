"""Explicit index requests share the observer's durable queue and incremental writer."""

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from .. import auth, database, note_search_jobs
from ..config import Config, InvalidInput
from ..note_search_cache import IndexUnavailable
from . import explicit_refresh, index, store


def submit(
    config: Config, client: str, identity: str, *, reobserve: bool = False
) -> dict[str, Any]:
    """Coalesce clicks; never scan the Vault or start a second HTTP-owned worker."""
    with database.locked(config), database.connect(config.state_root) as db:
        auth.grant(db, client, identity, "query")
        library = store.library(config, identity)
        if library is None:
            raise IndexUnavailable("note_index_update_required")
        prior = note_search_jobs.read(config, identity)
        # A committed recovery can replace the failed job with source_changed
        # without releasing its separate index cooldown. Only a still-future
        # failed-attempt cooldown distinguishes that job from active source work.
        failed_pending = (
            reobserve
            and prior is not None
            and prior["state"] == "pending"
            and prior["reason"] == "source_changed"
            and library["index_attempts"] > 0
            and library["index_after"] > time.time()
        )
        active = (
            prior is not None and prior["state"] in {"pending", "building"} and not failed_pending
        )
        if not active:
            # A failed attempt is quiescent, so an explicit request permits one retry.
            # Keep an unfinished refresh's identity: its package scan must still finish
            # for the authorized intent owner, even if a query-only peer clicks retry.
            retry_id = (
                prior["id"]
                if prior is not None
                and (prior["state"] == "waiting" or failed_pending)
                and not explicit_refresh.ready(config, identity)
                else None
            )
            note_search_jobs.record(
                config, identity, "pending", reason="explicit_update", expected_id=retry_id
            )
            with store.connection(config, write=True) as side:
                assert side is not None
                # An explicit request allows one retry of known per-note failures.
                # Repeated clicks on the active request do not erase cooldowns.
                side.execute("UPDATE issues SET next_check=0 WHERE vault=?", (identity,))
                side.execute(
                    "UPDATE libraries SET state='index_pending',index_after=0,error=NULL "
                    "WHERE id=?",
                    (identity,),
                )
        if active:
            # A crash may leave a building marker after an already-published slice.
            # Requeue only a quiescent catalog; preserve active work and retry cooldowns.
            with store.connection(config, write=True) as side:
                assert side is not None
                side.execute(
                    "UPDATE libraries SET state='index_pending' WHERE id=? "
                    "AND state IN ('ready','attention')",
                    (identity,),
                )
        job = note_search_jobs.read(config, identity)
        assert job is not None
        if reobserve:
            explicit_refresh.request(config, db, client, identity, job["id"])
        from . import dependencies

        blocked = dependencies.refresh(config, identity)
        dependency = dependencies.current(config, identity) if blocked else None
        return {
            "state": "blocked" if blocked else job["state"],
            **({"dependency": dependency} if dependency else {}),
            "vault_id": identity,
            "job_id": job["id"],
            "coalesced": active,
            "model_calls": 0,
            "index_mode": "persistent_chunks",
            "reconciling_metadata": reobserve,
        }


def refresh(
    config: Config,
    client: str,
    identity: str,
    cancelled: Callable[[sqlite3.Connection], bool] | None = None,
) -> dict[str, object]:
    """Compatibility for synchronous business callers; incomplete work is never success.

    The HTTP action uses submit instead. Each slice releases its lock, and the observer
    continues any remaining work after this bounded foreground attempt ends.
    """
    submit(config, client, identity)
    deadline = time.monotonic() + 30
    while True:
        if cancelled is not None:
            with database.connect(config.state_root) as db:
                if cancelled(db):
                    raise InvalidInput("note_index_cancelled")
        result = index.update(config, client, identity, cancelled=cancelled)
        if not result["pending_notes"]:
            if result["needs_attention"]:
                raise IndexUnavailable("note_index_update_required")
            return result
        if time.monotonic() >= deadline:
            raise IndexUnavailable("note_index_update_required")
