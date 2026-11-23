"""CAS current physical registrations; original publication evidence remains unchanged."""

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from .. import database, note_identity_store, publication_location_selection
from ..config import IntegrityError
from ..inputs import canonical
from . import inbox_review_plan as plans


def _registrations(config: Any, value: Any) -> list[Any]:
    return [
        asdict(row)
        for row in note_identity_store.records(config, value["vault_id"])
        if row.relative_path == value["path"]
    ]


def check(config: Any, db: Any, value: Any) -> None:
    if canonical(
        publication_location_selection.affected(db, value["vault_id"], {value["path"]})
    ) != canonical(value["locations_before"]):
        raise IntegrityError("inbox_review_publication_changed")
    if canonical(_registrations(config, value)) != canonical(value["registrations_before"]):
        raise IntegrityError("inbox_review_registration_changed")


def commit(config: Any, db: Any, value: Any) -> None:
    path, result = value["path"], value["result_evidence"]
    if "locations_after" not in value:
        after = deepcopy(value["locations_before"])
        for row in after:
            if row["state"] != "published":
                raise IntegrityError("inbox_review_publication_not_committed")
            plan = row["plan"]
            plan.setdefault("claims", {})[path] = result["key"][:2]
            plan.setdefault("file_keys", {})[path] = result["key"]
            plan.setdefault("write_hashes", {})[path] = result["sha256"]
            row["revision"] += 1
            row["value"] = canonical(plan)
        registrations = deepcopy(value["registrations_before"])
        for row in registrations:
            row.update(
                sha256=result["sha256"], file_key=result["key"], revision=row["revision"] + 1
            )
        value.update(locations_after=after, registrations_after=registrations)
        # Persist exact expected after-values before either database is changed.
        plans.save(config, value, "applying")
    with database.transaction(db):
        current = publication_location_selection.affected(db, value["vault_id"], {path})
        if canonical(current) != canonical(value["locations_after"]):
            if canonical(current) != canonical(value["locations_before"]):
                raise IntegrityError("inbox_review_publication_changed")
            for before, after in zip(
                value["locations_before"], value["locations_after"], strict=True
            ):
                changed = db.execute(
                    "UPDATE publication_locations SET value=?,revision=? WHERE kind=? "
                    "AND publication_id=? AND revision=? AND value=? AND state=?",
                    (
                        after["value"],
                        after["revision"],
                        before["kind"],
                        before["publication_id"],
                        before["revision"],
                        before["value"],
                        before["state"],
                    ),
                )
                if changed.rowcount != 1:
                    raise IntegrityError("inbox_review_publication_changed")
    registered = {row["identity"]: row for row in _registrations(config, value)}
    if set(registered) != {row["identity"] for row in value["registrations_before"]}:
        raise IntegrityError("inbox_review_registration_changed")
    for before, after in zip(
        value["registrations_before"], value["registrations_after"], strict=True
    ):
        current_row = registered[before["identity"]]
        if canonical(current_row) == canonical(after):
            continue
        if canonical(current_row) != canonical(before):
            raise IntegrityError("inbox_review_registration_changed")
        registration = note_identity_store.Registration(
            after["identity"],
            after["publication_id"],
            path,
            after["sha256"],
            tuple(after["file_key"]),
            tuple(after["vault_key"]),
            after["revision"],
        )
        note_identity_store.save(
            config,
            value["vault_id"],
            value["client_id"],
            registration,
            expected_revision=before["revision"],
        )
    value["metadata_committed"] = True
    plans.save(config, value, "applying")
