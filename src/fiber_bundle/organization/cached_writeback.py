"""Resume completed classification decisions without admitting another model call."""

import json
import sqlite3
from typing import Any

from .. import auth
from ..config import IntegrityError, StorageError
from . import store


def items(job: Any) -> list[Any]:
    return [i for i in job.get("items", []) if i.get("state") in {"failed", "blocked"}]


def eligible(job: Any) -> bool:
    pending = items(job)
    return bool(
        not job.get("maintenance")
        and job.get("state") in {"failed", "partial", "cancelled", "blocked"}
        and pending
        and all(i.get("evaluated") or i.get("selected") for i in pending)
    )


def available(config: Any, job: Any) -> bool:
    """Read-only eligibility hint; source/policy checks still run before writing."""
    if not eligible(job):
        return False
    analyzed = [i for i in items(job) if i.get("model_used") or i.get("model_called")]
    if any(
        type(i.get("body_windows")) is not int
        or i["body_windows"] < 1
        or not i.get("model_configuration_sha256")
        for i in analyzed
    ):
        return False
    count = sum(i["body_windows"] for i in analyzed)
    if not count:
        return True
    try:
        with store.connection(config) as db:
            if (
                db is None
                or not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='model_reviews'"
                ).fetchone()
            ):
                return False
            rows = db.execute(
                "SELECT json_extract(value,'$.state'),"
                "json_extract(value,'$.evidence_validation.state') FROM model_reviews "
                "WHERE vault=? AND json_valid(value) AND json_extract(value,'$.job')=?",
                (job["vault_id"], job["id"]),
            ).fetchall()
        return bool(
            sum(state == "succeeded" and validation == "validated" for state, validation in rows)
            >= count
        )
    except (OSError, ValueError, TypeError, sqlite3.Error, StorageError):
        return False


def verify(config: Any, db: Any, job: Any, entity: Any, mocs: Any, item: Any) -> None:
    if job.get("resume_mode") != "cached_writeback":
        return
    if not (item.get("evaluated") or item.get("selected")):
        raise IntegrityError("organization_model_cache_incomplete")
    if not item.get("model_used") and not item.get("model_called"):
        return
    if not item.get("model_used") or type(item.get("body_windows")) is not int:
        raise IntegrityError("organization_model_cache_incomplete")
    from . import jobs, model_review

    def check() -> None:
        for permission in ("query", "add"):
            auth.grant(db, job["client_id"], job["vault_id"], permission)
        if jobs.policy(config, job["vault_id"])["revision"] != job["policy"]["revision"]:
            raise IntegrityError("organization_policy_changed")

    check()
    result = model_review.resolve(config, job, entity, mocs, item, check, cached_only=True)
    if (
        result["selected"] != item["selected"]
        or result["body_windows"] != item.get("body_windows")
        or result["model_configuration_sha256"] != item.get("model_configuration_sha256")
        or json.dumps(result["model_evidence"], sort_keys=True)
        != json.dumps(item.get("model_evidence"), sort_keys=True)
    ):
        raise IntegrityError("organization_cached_decision_changed")
