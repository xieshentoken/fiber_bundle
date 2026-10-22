"""Cheap local metadata versions for optimistic, read-only storage boundaries."""

from typing import Any

from .organization import store


def stamp(config: Any, vault: str) -> Any:
    root = config.state_root
    paths = [
        p
        for p in root.iterdir()
        if p.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-journal", ".json"))
        and not p.name.startswith(("message-channels.sqlite3", "organization.sqlite3"))
        and p.name not in {"backend.json"}
    ]
    for name in ("configs", "query-policies", "model-secrets"):
        directory = root / name
        if directory.exists():
            paths.extend(p for p in directory.iterdir() if p.is_file())
    for name in ("moc-files", "moc-note-files"):
        directory = root / name
        if directory.exists():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    result: dict[str, Any] = {
        str(p): (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        for p in paths
        for s in [p.stat()]
    }
    # Book discovery updates its display status independently of organization.
    # Semantic package/decision/job changes still invalidate the read, including
    # additions and deletions. Store writes always advance each row's `updated`.
    with store.connection(config) as db:
        if db is not None:
            info = (root / "organization.sqlite3").stat()
            result["organization_identity"] = (info.st_dev, info.st_ino)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["organization"] = {
                table: [
                    tuple(r)
                    for r in db.execute(
                        f"SELECT id,updated FROM {table} WHERE vault=? ORDER BY id", (vault,)
                    )
                ]
                for table in sorted((store.TABLES & tables) - {"package_scans"})
            }
    return result
