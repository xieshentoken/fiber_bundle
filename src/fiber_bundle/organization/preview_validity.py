"""Dependency validation for frozen previews; elapsed time never invalidates them."""

from typing import Any

from .. import auth
from ..config import IntegrityError, InvalidInput


def validate_inputs(config: Any, db: Any, job: Any, entities: Any, mocs: Any) -> None:
    from . import manual_resolution
    from .maintenance_moc_recovery import manual_change

    handled = manual_resolution.resolved(config, job)
    expected_ids = {i["entity"] for i in job["items"]} - handled
    current_ids = {e["id"] for e in entities} - handled
    # Cache recovery intentionally covers only its selected unresolved entities.
    if not job.get("cache_recovery") and current_ids != expected_ids:
        raise IntegrityError("maintenance_preview_changed")
    prior = {m["id"]: m for m in job["mocs"] if not m.get("new")}
    current = {m["id"]: m for m in mocs}
    if prior.keys() != current.keys():
        raise IntegrityError("maintenance_preview_changed")
    for mid, moc in current.items():
        before = prior[mid]
        fields = ("sha256", "revision", "members")
        expected = {k: before[k] for k in fields}
        actual = {k: moc[k] for k in fields}
        if moc["path"] != before["path"] or (
            actual != expected and not manual_change(config, db, job, mid, actual, expected)
        ):
            raise IntegrityError("maintenance_preview_changed")


def status(config: Any, db: Any, job: Any) -> dict[str, Any]:
    from .maintenance import validate

    try:
        validate(config, db, job)
    except (IntegrityError, InvalidInput) as error:
        return {"state": "invalidated", "reason": str(error)}
    except auth.Denied:
        return {"state": "invalidated", "reason": "maintenance_authorization_or_policy_changed"}
    return {"state": "valid"}
