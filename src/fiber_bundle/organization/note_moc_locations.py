"""Advance managed publication identity and moc-only baseline after a verified note exchange."""

import base64
from typing import Any

from .. import database, publication_locations
from ..config import IntegrityError
from ..inputs import canonical
from ..objects import digest
from . import note_moc_fields as fields


def apply(
    db: Any,
    vault: str,
    record: Any,
    physical: list[Any],
    wanted: list[str],
    aliases: dict[str, str],
) -> None:
    path = record["path"]
    managed = publication_locations.inventory(db, vault)[0].get(path)
    if managed is None:
        return
    if managed["identity"] != record["entity"] or managed["kind"] == "expert":
        raise IntegrityError("note_moc_managed_identity_conflict")
    location = publication_locations.get(db, managed["kind"], managed["publication_id"])
    if location is None or location["state"] != "published":
        raise IntegrityError("note_moc_managed_location_unavailable")
    plan = location["plan"]
    previous = plan["file_keys"][path]
    if previous == physical:
        return
    if previous not in [v["file_key"] for v in record["versions"]]:
        raise IntegrityError("note_moc_managed_location_changed")
    before = location["value"]
    # Advance the generated baseline only by the controlled property edit; user
    # body edits remain differences from that baseline, just as for link moves.
    baseline = base64.b64decode(plan["outputs"][path], validate=True)
    projected, _ = fields.render(
        baseline,
        wanted,
        record.get("prior_owned", []) + record["owned"],
        lambda v: aliases.get(fields.target(v), fields.target(v)),
    )
    plan["outputs"][path] = base64.b64encode(projected).decode()
    plan["published_hashes"][path] = digest(projected)
    plan["file_keys"][path] = physical
    plan["claims"][path] = physical[:2]
    if "write_hashes" in plan:
        plan["write_hashes"][path] = record["after_sha256"]
    if path == location["entry_note_path"] and "source_spans" in plan:
        plan["source_spans"].update(
            status="moc_properties_offsets_unavailable",
            published_units=None,
            published_note_sha256=digest(projected),
        )
    after = canonical(plan)
    with database.transaction(db):
        changed = db.execute(
            "UPDATE publication_locations SET value=?,revision=revision+1 "
            "WHERE kind=? AND publication_id=? AND vault_id=? AND revision=? AND value=?",
            (
                after,
                managed["kind"],
                managed["publication_id"],
                vault,
                location["revision"],
                before,
            ),
        ).rowcount
        if changed != 1:
            raise IntegrityError("note_moc_managed_location_changed")
    record.setdefault("locations", []).append(
        {
            "kind": managed["kind"],
            "publication_id": managed["publication_id"],
            "before_revision": location["revision"],
            "after_revision": location["revision"] + 1,
            "before_sha256": digest(before.encode()),
            "after_sha256": digest(after.encode()),
        }
    )
