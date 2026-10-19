"""Test-root Vault reservation, identity checks and explicit local approval."""

import json
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from fiber_bundle import auth, database
from fiber_bundle.config import Config, IntegrityError, InvalidInput, contained, sync_directory
from fiber_bundle.inputs import text

CREATE_ROOT = "test vault"


def checked_vault(
    config: Config,
    row: sqlite3.Row,
    *,
    pending: bool = False,
    metadata: sqlite3.Connection | None = None,
) -> Path:
    if row["status"] not in ({"active", "pending"} if pending else {"active"}):
        raise InvalidInput("vault is not active")
    if row["attachment_state"] != "attached":
        raise auth.Denied(404)
    from .vault_relocation.guard import check_root

    check_root(config, row, metadata)
    name, relative = row["name"], row["relative_path"]
    if row["root_kind"] != "legacy":
        from .vault_catalog import checked_root

        path = checked_root(config, row)
        return path
    if name in dict(config.vaults):
        if relative != dict(config.vaults)[name] or row["requested_by_client_id"] is not None:
            raise IntegrityError("configured vault identity changed")
    else:
        try:
            identity = str(UUID(row["id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise IntegrityError("invalid vault identity") from exc
        if relative != f"{CREATE_ROOT}/created-{identity}" or not row["requested_by_client_id"]:
            raise IntegrityError("invalid generated vault path")
    path = config.vault_path(relative)
    from .cloud_files import require_available
    from .vault_scope import for_vault

    scope = for_vault(path, row)
    require_available(path, tree=not scope.exclude and scope.include == ("",))
    state = config.state_root
    if state == path or state in path.parents or path in state.parents:
        raise IntegrityError("state overlaps vault")
    if not path.is_dir():
        raise IntegrityError("vault directory missing")
    if name not in dict(config.vaults) or row["directory_dev"] is not None:
        info = path.stat()
        if (info.st_dev, info.st_ino) != (row["directory_dev"], row["directory_ino"]):
            raise IntegrityError("vault directory identity changed")
    return path


def _create_directory(config: Config, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    path = contained(config.project_root, row["relative_path"])
    # Exclusive mkdir is deliberate: never adopt an existing or crash-orphan directory.
    path.mkdir(mode=0o700)
    sync_directory(path.parent)
    info = path.stat()
    with database.transaction(connection):
        connection.execute(
            "UPDATE vaults SET directory_dev=?,directory_ino=? WHERE id=?",
            (info.st_dev, info.st_ino, row["id"]),
        )


def reserve(config: Config, connection: sqlite3.Connection, client_id: str, name: str) -> str:
    text(name, limit=100)
    if connection.execute("SELECT 1 FROM vaults WHERE name=?", (name,)).fetchone():
        raise InvalidInput("vault name already exists")
    identity = str(uuid4())
    relative = f"{CREATE_ROOT}/created-{identity}"
    root = contained(config.project_root, CREATE_ROOT)
    state = config.state_root
    if state == root or state in root.parents or root in state.parents:
        raise InvalidInput("creation root overlaps state")
    with database.transaction(connection):
        connection.execute(
            "INSERT INTO vaults(id,name,relative_path,status,requested_by_client_id) "
            "VALUES(?,?,?,'pending',?)",
            (identity, name, relative, client_id),
        )
    row = connection.execute("SELECT * FROM vaults WHERE id=?", (identity,)).fetchone()
    _create_directory(config, connection, row)
    return identity


def prepare_approval(config: Config, connection: sqlite3.Connection, identity: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM vaults WHERE id=?", (identity,)).fetchone()
    if row is None or row["status"] != "pending":
        raise InvalidInput("unknown pending vault")
    # Resume only when the reserved directory was never created. An existing unrecorded
    # directory is ambiguous and remains pending for local investigation.
    if row["directory_ino"] is None:
        expected = f"{CREATE_ROOT}/created-{UUID(row['id'])}"
        if row["relative_path"] != expected:
            raise IntegrityError("invalid reserved path")
        _create_directory(config, connection, row)
        row = connection.execute("SELECT * FROM vaults WHERE id=?", (identity,)).fetchone()
    path = checked_vault(config, row, pending=True)
    if any(path.iterdir()):
        raise InvalidInput("approval requires an empty vault")
    if not isinstance(row, sqlite3.Row):
        raise IntegrityError("invalid database row")
    return row


def list_vaults(connection: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        {
            **dict(row),
            "status": row["status"]
            if row["attachment_state"] == "attached"
            else row["attachment_state"],
        }
        for row in connection.execute(
            "SELECT "
            "id,name,status,default_query,requested_by_client_id,absolute_root,"
            "root_kind,read_only,attachment_state,scope_json "
            "FROM vaults ORDER BY name"
        )
    ]


def client_vaults(connection: sqlite3.Connection, client_id: str) -> list[dict[str, object]]:
    managed = connection.execute(
        "SELECT state,configuration_json FROM agent_connections WHERE client_id=?", (client_id,)
    ).fetchone()
    if managed is not None and managed["state"] != "enabled":
        return []
    scope = json.loads(managed["configuration_json"])["vault_ids"] if managed is not None else None
    return [
        dict(row)
        for row in connection.execute(
            "SELECT v.id,v.name,v.status,v.default_query,g.can_query,g.can_original,g.can_add "
            "FROM vaults v JOIN client_vault_grants g ON g.vault_id=v.id "
            "WHERE g.client_id=? AND v.status='active' AND v.attachment_state='attached' "
            "AND (g.can_query=1 OR g.can_original=1 OR g.can_add=1) ORDER BY v.name",
            (client_id,),
        )
        if scope is None or row["id"] in scope
    ]


def request_vault(config: Config, connection: sqlite3.Connection, client_id: str, name: str) -> str:
    row = connection.execute(
        "SELECT create_vault FROM clients WHERE id=? AND active=1",
        (client_id,),
    ).fetchone()
    if row is None or row["create_vault"] != 1:
        raise auth.Denied()
    return reserve(config, connection, client_id, name)


def set_permissions(
    config: Config,
    connection: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    *,
    query: bool,
    original: bool,
    add: bool,
) -> None:
    managed = connection.execute(
        "SELECT state,configuration_json FROM agent_connections WHERE client_id=?", (client_id,)
    ).fetchone()
    if managed and (
        original
        or add
        or vault_id not in json.loads(managed["configuration_json"])["vault_ids"]
        or (query and managed["state"] != "enabled")
    ):
        raise InvalidInput("agent_permissions_owned")
    row = connection.execute("SELECT * FROM vaults WHERE id=?", (vault_id,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    checked_vault(config, row)
    auth.set_grant(connection, client_id, vault_id, query=query, original=original, add=add)


def set_default_query(connection: sqlite3.Connection, vault_id: str, enabled: bool) -> None:
    from .vault_merge_guard import check

    check(connection, vault_id)
    if not connection.execute(
        "UPDATE vaults SET default_query=? WHERE id=? AND status='active' "
        "AND attachment_state='attached'",
        (int(enabled), vault_id),
    ).rowcount:
        raise auth.Denied(404)


def approve(
    config: Config,
    connection: sqlite3.Connection,
    vault_id: str,
    client_id: str,
    *,
    query: bool,
    original: bool,
    add: bool,
) -> None:
    prepare_approval(config, connection, vault_id)
    with database.transaction(connection):
        connection.execute("UPDATE vaults SET status='active' WHERE id=?", (vault_id,))
        auth.set_grant(connection, client_id, vault_id, query=query, original=original, add=add)


def require_scope(config: Config, row: sqlite3.Row, relative: str) -> None:
    """Explicit file writes must obey the same catalog scope before probing their target."""
    from .vault_scope import for_vault, prefix

    path = prefix(relative)
    if not path or not for_vault(checked_vault(config, row), row).allows(path):
        raise InvalidInput("vault_scope_denied")
