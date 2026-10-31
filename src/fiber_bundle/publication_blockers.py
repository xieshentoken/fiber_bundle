"""Read-only publication dependencies; never clears or retries an owning operation."""

import sqlite3


def pending(db: sqlite3.Connection, vault: str) -> list[dict[str, str]]:
    return [
        {"id": row[0], "kind": row[1], "state": row[2]}
        for row in db.execute(
            "SELECT id,kind,state FROM vault_operations WHERE vault_id=? AND kind IN "
            "('note_placement','layout_migration','publication_rebind','maintenance_link_repair') "
            "AND state IN ('applying','reconcile') ORDER BY created_at,id LIMIT 8",
            (vault,),
        )
    ]
