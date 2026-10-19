"""Local password sessions and independent client capabilities; caller holds storage lock."""

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from uuid import uuid4

from fiber_bundle import database
from fiber_bundle.config import Config, IntegrityError, InvalidInput
from fiber_bundle.inputs import text

COOKIE = "fiber_session"


class Denied(Exception):
    def __init__(self, status: int = 403):
        self.status = status
        super().__init__("request denied")


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def set_password(config: Config, password: str) -> None:
    if not 12 <= len(password) <= 1024:
        raise InvalidInput("password must have 12 to 1024 characters")
    salt = secrets.token_bytes(32)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    with database.locked(config):
        connection = database.connect(config.state_root)
        try:
            with database.transaction(connection):
                connection.execute(
                    "INSERT INTO admin_auth VALUES(1,?,?,16384,8,1,1) "
                    "ON CONFLICT(id) DO UPDATE SET salt=excluded.salt, "
                    "password_hash=excluded.password_hash,n=excluded.n,r=excluded.r,p=excluded.p,"
                    "version=admin_auth.version+1",
                    (salt, derived),
                )
                connection.execute("DELETE FROM admin_sessions")
        finally:
            connection.close()


def login(connection: sqlite3.Connection, password: str, ttl: int) -> tuple[str, str, float]:
    try:
        password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidInput("invalid password text") from exc
    row = connection.execute("SELECT * FROM admin_auth WHERE id=1").fetchone()
    if row is None:
        raise Denied(401)
    if (row["n"], row["r"], row["p"]) != (16384, 8, 1) or len(row["salt"]) != 32:
        raise IntegrityError("invalid password parameters")
    derived = hashlib.scrypt(password.encode(), salt=row["salt"], n=16384, r=8, p=1)
    if not hmac.compare_digest(derived, row["password_hash"]):
        raise Denied(401)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = time.time() + ttl
    with database.transaction(connection):
        connection.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (time.time(),))
        connection.execute(
            "INSERT INTO admin_sessions VALUES(?,?,?,?)",
            (digest(token), digest(csrf), expires, row["version"]),
        )
    return token, csrf, expires


def admin(connection: sqlite3.Connection, token: str, csrf: str | None = None) -> sqlite3.Row:
    row = connection.execute(
        "SELECT s.* FROM admin_sessions s JOIN admin_auth a ON "
        "a.version=s.credential_version WHERE a.id=1 AND s.token_digest=? AND s.expires_at>?",
        (digest(token), time.time()),
    ).fetchone()
    if row is None:
        raise Denied(401)
    if csrf is not None:
        supplied = digest(csrf)
        # Existing login tokens remain valid until their session ends. Restoring another
        # window must not revoke them; the derived token shares exactly that lifetime.
        initial = hmac.compare_digest(row["csrf_digest"], supplied)
        restored = hmac.compare_digest(digest(_session_csrf(row, token)), supplied)
        if not (initial | restored):
            raise Denied()
    if not isinstance(row, sqlite3.Row):
        raise IntegrityError("invalid database row")
    return row


def _session_csrf(row: sqlite3.Row, token: str) -> str:
    # The stored random-token digest is a per-session secret, never exposed by HTTP.
    # A domain-separated HMAC binds restoration to this cookie without storing plaintext.
    return hmac.new(
        row["csrf_digest"].encode(), b"fiber-admin-csrf/v1\0" + token.encode(), hashlib.sha256
    ).hexdigest()


def resume_session(connection: sqlite3.Connection, token: str) -> tuple[str, float]:
    """Recover this live session's stable CSRF token without writes or TTL extension."""
    row = admin(connection, token)
    return _session_csrf(row, token), float(row["expires_at"])


def client(connection: sqlite3.Connection, authorization: str) -> sqlite3.Row:
    if not authorization.startswith("Bearer ") or len(authorization) > 256:
        raise Denied(401)
    row = connection.execute(
        "SELECT * FROM clients WHERE token_digest=? AND active=1",
        (digest(authorization[7:]),),
    ).fetchone()
    if row is None:
        raise Denied(401)
    if not isinstance(row, sqlite3.Row):
        raise IntegrityError("invalid database row")
    return row


def grant(
    connection: sqlite3.Connection, client_id: str, vault_id: str, action: str
) -> sqlite3.Row:
    from .vault_merge_guard import check as merge_check

    merge_check(connection, vault_id)
    if action not in {"query", "original", "add"}:
        raise InvalidInput("unknown capability")
    managed = connection.execute(
        "SELECT state,configuration_json FROM agent_connections WHERE client_id=?", (client_id,)
    ).fetchone()
    if managed is not None:
        configuration = json.loads(managed["configuration_json"])
        original_allowed = (
            action == "original"
            and configuration.get("schema_version") == "fiber-agent-connection/v2"
            and configuration.get("allow_originals") is True
        )
        if (
            (action != "query" and not original_allowed)
            or managed["state"] != "enabled"
            or vault_id not in configuration["vault_ids"]
        ):
            raise Denied(404)
    row = connection.execute(
        "SELECT v.*,g.can_query,g.can_original,g.can_add FROM vaults v "
        "JOIN client_vault_grants g ON v.id=g.vault_id JOIN clients c ON c.id=g.client_id "
        "WHERE c.id=? AND c.active=1 AND v.id=? AND v.status='active' "
        "AND v.attachment_state='attached'",
        (client_id, vault_id),
    ).fetchone()
    if row is None or row[f"can_{action}"] != 1 or (action == "add" and row["read_only"]):
        raise Denied(404)
    if not isinstance(row, sqlite3.Row):
        raise IntegrityError("invalid database row")
    return row


def create_client(connection: sqlite3.Connection, name: str, create_vault: bool) -> dict[str, str]:
    text(name, limit=100)
    identity, token = str(uuid4()), secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO clients(id,name,token_digest,create_vault,created_at) VALUES(?,?,?,?,?)",
        (identity, name, digest(token), int(create_vault), time.time()),
    )
    return {"id": identity, "name": name, "token": token}


def set_grant(
    connection: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    *,
    query: bool,
    original: bool,
    add: bool,
) -> None:
    from .vault_relocation.guard import check

    check(connection, vault_id)
    if (
        not connection.execute(
            "SELECT 1 FROM clients WHERE id=? AND active=1",
            (client_id,),
        ).fetchone()
        or not connection.execute(
            "SELECT 1 FROM vaults WHERE id=? AND status='active' AND "
            "attachment_state='attached' AND (?=0 OR read_only=0)",
            (vault_id, int(add)),
        ).fetchone()
    ):
        raise Denied(404)
    connection.execute(
        "INSERT INTO client_vault_grants VALUES(?,?,?,?,?) ON CONFLICT(client_id,vault_id) "
        "DO UPDATE SET can_query=excluded.can_query,can_original=excluded.can_original,"
        "can_add=excluded.can_add",
        (client_id, vault_id, int(query), int(original), int(add)),
    )


def end_session(connection: sqlite3.Connection, token: str) -> None:
    connection.execute("DELETE FROM admin_sessions WHERE token_digest=?", (digest(token),))


def list_clients(connection: sqlite3.Connection) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in connection.execute(
        "SELECT c.id,c.name,c.active,c.create_vault,a.id AS managed_connection_id "
        "FROM clients c LEFT JOIN agent_connections a ON a.client_id=c.id "
        "ORDER BY c.created_at,c.id"
    ):
        item = dict(row)
        if item["managed_connection_id"] is None:
            item.pop("managed_connection_id")
        item["grants"] = [
            dict(grant)
            for grant in connection.execute(
                "SELECT g.vault_id,g.can_query,g.can_original,g.can_add,"
                "coalesce(e.revision,0) AS permission_revision FROM client_vault_grants g "
                "LEFT JOIN graph_permission_epochs e ON e.client_id=g.client_id "
                "AND e.vault_id=g.vault_id WHERE g.client_id=?",
                (row["id"],),
            )
        ]
        result.append(item)
    return result


def revoke_client(connection: sqlite3.Connection, client_id: str) -> None:
    if not connection.execute(
        "UPDATE clients SET active=0,revoked_at=? WHERE id=?", (time.time(), client_id)
    ).rowcount:
        raise Denied(404)
