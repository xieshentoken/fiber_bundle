"""Best-effort terminal batch notification, after native journals and commits have settled."""

import hashlib
import logging
from typing import Any

from ..config import Config
from ..inputs import canonical
from . import store


def completed(config: Config, identity: str, payload: Any) -> None:
    try:
        value = store.read(config)
        if not value["policy"]["enabled"] or not value["policy"]["after_moc"]:
            return
        token = hashlib.sha256(canonical([identity, payload]).encode()).hexdigest()
        with store.edit(config) as state:
            seen = state.setdefault("moc_seen", {})
            if seen.get(identity) != token:
                state.setdefault("pending_moc", []).append(token)
                if not identity.startswith("manual:"):
                    seen[identity] = token
    except Exception:
        logging.getLogger(__name__).error("MOC completed; backup notification needs attention")


def organization(config: Config, jobs: dict[str, Any]) -> None:
    for identity, job in jobs.items():
        if job.get("state") not in {"completed", "partial"}:
            continue
        items = job.get("items", [])
        if any(i.get("state") == "pending" for i in items):
            continue
        if not (
            any(i.get("changed") or i.get("archive_operation") for i in items)
            or job.get("new_mocs")
        ):
            continue
        payload = [
            [i.get("entity"), i.get("state"), i.get("changed"), i.get("archive_operation")]
            for i in items
        ]
        completed(config, "organization:" + identity, [payload, job.get("new_mocs", [])])


def catalogue(config: Config) -> dict[str, Any]:
    """Terminal native receipts are the durable outbox; never infer completion from a page."""
    from ..organization import store as organization_store

    result = {}
    for job in organization_store.rows(config, "jobs"):
        items = job.get("items", [])
        if (
            job.get("state") in {"completed", "partial"}
            and not any(i.get("state") == "pending" for i in items)
            and (
                any(i.get("changed") or i.get("archive_operation") for i in items)
                or job.get("new_mocs")
            )
        ):
            payload = [
                [i.get("entity"), i.get("state"), i.get("changed"), i.get("archive_operation")]
                for i in items
            ]
            result["organization:" + job["id"]] = [payload, job.get("new_mocs", [])]
    return result


def seed(config: Config) -> dict[str, str]:
    return {
        identity: hashlib.sha256(canonical([identity, payload]).encode()).hexdigest()
        for identity, payload in catalogue(config).items()
    }


def reconcile(config: Config) -> None:
    for identity, payload in catalogue(config).items():
        completed(config, identity, payload)
