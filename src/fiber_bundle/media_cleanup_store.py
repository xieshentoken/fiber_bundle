"""Explicit tombstones for published linked media; direct/shared originals never qualify."""

import sqlite3


def cleaned(db: sqlite3.Connection, asset_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM media_cleanup WHERE state IN ('deleting','complete') "
            "AND json_extract(manifest_json,'$.asset_id')=? "
            "AND json_extract(manifest_json,'$.source_object_deleted')=1",
            (asset_id,),
        ).fetchone()
        is not None
    )
