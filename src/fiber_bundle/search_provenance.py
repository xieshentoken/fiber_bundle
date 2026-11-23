"""One search's compact channel provenance, read under the caller's storage lease."""

import json
import sqlite3
from typing import Any


def intake_locations(
    db: sqlite3.Connection,
    vault_id: str,
    *,
    selected_paths: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project only evidence fields; large output/recovery blobs stay in SQLite.

    Match the existing provenance lookup's vault index order and first match,
    including supplements found through published_hashes rather than entry paths.
    Nothing is retained across searches or independent write units.
    """
    result: dict[str, dict[str, Any]] = {}
    if selected_paths is not None and not selected_paths:
        return result
    selected_sql = ""
    parameters = [vault_id]
    if selected_paths is not None:
        selected_sql = (
            " AND (entry_note_path IN (SELECT value FROM json_each(?)) OR EXISTS("
            "SELECT 1 FROM json_each(CASE WHEN json_valid(value) THEN value ELSE '{}' END,"
            "'$.published_hashes') h WHERE h.key IN (SELECT value FROM json_each(?))))"
        )
        paths = json.dumps(sorted(selected_paths))
        parameters.extend([paths, paths])
    for row in db.execute(
        "SELECT publication_id,entry_note_path,"
        "json_extract(value,'$.published_hashes') AS published_hashes,"
        "json_extract(value,'$.intake_source') AS intake_source,"
        "json_extract(value,'$.mapping') AS mapping,"
        "json_extract(value,'$.supplement_roles') AS supplement_roles "
        "FROM publication_locations WHERE vault_id=? AND kind='intake' AND state='published'"
        + selected_sql,
        parameters,
    ):
        plan = {
            key: json.loads(row[key]) if row[key] is not None else default
            for key, default in (
                ("published_hashes", {}),
                ("intake_source", None),
                ("mapping", {}),
                ("supplement_roles", {}),
            )
        }
        location = {"publication_id": row["publication_id"], "plan": plan}
        for path in (row["entry_note_path"], *plan["published_hashes"]):
            if selected_paths is not None and path not in selected_paths:
                continue
            result.setdefault(path, location)
    return result
