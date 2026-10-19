"""Persistent coalescing triggers, evaluated only by the backend runtime."""

import datetime as dt
import time
from typing import Any

from ..config import Config
from . import changes, events, store


def due(value: dict[str, Any], now: float) -> str | None:
    policy = value["policy"]
    if not policy["enabled"]:
        return None
    if policy["after_moc"] and value.get("pending_moc"):
        return "moc_batch"
    if value["dirty_since"] is None:
        return None
    baseline = value["baseline"]["at"] if value["baseline"] else value.get("configured_at", now)
    if policy["on_change"]:
        delay = policy["debounce_seconds"]
        if now - value.get("last_change", now) >= delay or now - value["dirty_since"] >= max(
            900, 3 * delay
        ):
            return "business_change"
    if policy["growth_mb"] and value["growth"] >= policy["growth_mb"] * 1024**2:
        return "data_growth"
    if policy["interval_minutes"] and now - baseline >= policy["interval_minutes"] * 60:
        return "interval"
    if policy["daily_time"]:
        local = dt.datetime.fromtimestamp(now).astimezone()
        hour, minute = map(int, policy["daily_time"].split(":"))
        cutoff = local.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
        if now >= cutoff and baseline < cutoff:
            return "daily"
    return None


def tick(
    config: Config, *, now: float | None = None, observation_cache: dict[str, Any] | None = None
) -> None:
    value = store.read(config)
    if not value["policy"]["enabled"]:
        return
    now = time.time() if now is None else now
    if now - value.get("checked_at", 0) < 30:
        return
    events.reconcile(config)
    current = (
        changes.observe(config)
        if observation_cache is None
        else changes.observe(config, observation_cache)
    )
    with store.edit(config) as state:
        # Policy edits racing a scan must not admit work under a stale configuration.
        if state["revision"] != value["revision"] or not state["policy"]["enabled"]:
            return
        old = state["observed"] or (state["baseline"]["observation"] if state["baseline"] else None)
        if old is None or old["sha256"] != current["sha256"]:
            state["growth"] += changes.growth(old, current)
            state["last_change"] = now
            if state["dirty_since"] is None:
                state["dirty_since"] = now
        state.update(observed=current, checked_at=now)
        reason = due(state, now)
        # A failed run remains actionable until explicit retry or the finite retry loop;
        # unchanged data cannot create an endless sequence of new failing jobs.
        failed = next((j for j in reversed(state["jobs"]) if j["state"] == "failed"), None)
        if (
            failed
            and failed["revision"] == state["revision"]
            and (failed.get("observation") or {}).get("sha256") == current["sha256"]
        ):
            reason = None
    if reason:
        store.enqueue(config, reason, current)
