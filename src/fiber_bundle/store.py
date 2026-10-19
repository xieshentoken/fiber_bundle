"""M1 receipt, version, reference and recovery operations; no network or model calls."""

import errno
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fiber_bundle import database, objects
from fiber_bundle.config import (
    Config,
    IntegrityError,
    InvalidInput,
    StorageError,
    contained,
    sync_directory,
)
from fiber_bundle.inputs import AssetInput, canonical, manifest, parse_manifest, text


class EventConflict(StorageError):
    pass


@dataclass(frozen=True)
class Receipt:
    job_id: str
    state: str
    source_version_id: str | None
    error_code: str | None


def _receipt(row: sqlite3.Row) -> Receipt:
    if row["state"] not in {"receiving", "stored", "incomplete", "failed"}:
        raise IntegrityError("invalid receipt state")
    return Receipt(row["job_id"], row["state"], row["source_version_id"], row["error_code"])


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM receipts WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise InvalidInput("unknown receipt")
    if not isinstance(row, sqlite3.Row):
        raise IntegrityError("invalid database row")
    return row


class Store:
    """Trusted local storage; HTTP callers enter through authenticated operations."""

    def __init__(self, config: Config):
        config.validate()
        self.config = config

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with database.locked(self.config):
            connection = database.connect(self.config.state_root)
            try:
                yield connection
            finally:
                connection.close()

    def _vault_id(self, connection: sqlite3.Connection, name: str) -> str:
        text(name)
        from fiber_bundle.vaults import checked_vault

        row = connection.execute("SELECT * FROM vaults WHERE name=?", (name,)).fetchone()
        if row is None:
            raise InvalidInput("unknown vault")
        checked_vault(self.config, row)
        return str(row["id"])

    def _manifest(self, row: sqlite3.Row) -> dict[str, Any]:
        value = row["manifest_json"]
        if not isinstance(value, str) or objects.digest(value.encode()) != row["input_digest"]:
            raise IntegrityError("receipt input digest mismatch")
        return parse_manifest(value)

    def ingest(
        self,
        *,
        ingress_scope: str,
        event_id: str,
        vault: str,
        source_key: str,
        raw_event: bytes,
        items: tuple[AssetInput, ...],
        session_scope: str,
        session_id: str,
        retention: str = "session",
    ) -> Receipt:
        with self._connection() as connection:
            return self.ingest_locked(
                connection,
                ingress_scope=ingress_scope,
                event_id=event_id,
                vault=vault,
                source_key=source_key,
                raw_event=raw_event,
                items=items,
                session_scope=session_scope,
                session_id=session_id,
                retention=retention,
            )

    def ingest_locked(
        self,
        connection: sqlite3.Connection,
        *,
        ingress_scope: str,
        event_id: str,
        vault: str,
        source_key: str,
        raw_event: bytes,
        items: tuple[AssetInput, ...],
        session_scope: str,
        session_id: str,
        retention: str = "session",
        owner_client_id: str | None = None,
    ) -> Receipt:
        """Trusted caller holds database.locked; this method owns its transactions."""
        text(ingress_scope)
        text(event_id)
        value, all_items = manifest(
            vault=vault,
            source_key=source_key,
            session_scope=session_scope,
            session_id=session_id,
            retention=retention,
            raw_event=raw_event,
            items=items,
        )
        fingerprint = objects.digest(value.encode())
        vault_id = self._vault_id(connection, vault)
        from .vault_merge_guard import check as merge_check

        merge_check(connection, vault_id)
        with database.transaction(connection):
            row = connection.execute(
                "SELECT * FROM receipts WHERE ingress_scope=? AND event_id=?",
                (ingress_scope, event_id),
            ).fetchone()
            if row is not None and row["input_digest"] != fingerprint:
                raise EventConflict("event identity already has different input")
            if row is None:
                job_id = str(uuid4())
                connection.execute(
                    "INSERT INTO receipts(job_id,ingress_scope,event_id,input_digest,"
                    "manifest_json,state,created_at,owner_client_id) "
                    "VALUES(?,?,?,?,?,'receiving',?,?)",
                    (job_id, ingress_scope, event_id, fingerprint, value, _now(), owner_client_id),
                )
            else:
                job_id = row["job_id"]
        row = _row(connection, job_id)
        try:
            data = self._manifest(row)
            # Completed replays must not recreate a cleared session association.
            if row["state"] != "stored":
                for item in all_items:
                    if item.data is not None:
                        objects.put_object(self.config.state_root, vault_id, item.data)
            return self._finish(connection, row, data, vault_id)
        except (OSError, sqlite3.Error, StorageError) as exc:
            self._record_failure(connection, job_id, exc)
            raise

    def commit_prepared_locked(
        self,
        connection: sqlite3.Connection,
        *,
        ingress_scope: str,
        event_id: str,
        manifest_json: str,
        object_keys: dict[str, list[int]],
        owner_client_id: str,
    ) -> Receipt:
        """Commit internally prepared objects after streaming verification outside the lease.

        Only trusted storage callers use this method. All object descriptors and
        physical fingerprints are checked again; no arbitrary source path is accepted.
        """
        import stat

        data = parse_manifest(manifest_json)
        text(ingress_scope)
        text(event_id)
        vault_id = self._vault_id(connection, data["vault"])
        from .vault_merge_guard import check

        check(connection, vault_id)
        for asset in data["assets"]:
            relative = objects.object_relative(vault_id, asset["sha256"])
            info = contained(self.config.state_root, relative).lstat()
            key = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size != asset["byte_count"]
                or object_keys.get(relative) != key
            ):
                raise IntegrityError("prepared_object_changed")
        fingerprint = objects.digest(manifest_json.encode())
        with database.transaction(connection):
            row = connection.execute(
                "SELECT * FROM receipts WHERE ingress_scope=? AND event_id=?",
                (ingress_scope, event_id),
            ).fetchone()
            if row is None:
                job = str(uuid4())
                connection.execute(
                    "INSERT INTO receipts(job_id,ingress_scope,event_id,input_digest,m"
                    "anifest_json,state,created_at,owner_client_id) VALUES(?,?,?,?,?,'"
                    "receiving',?,?)",
                    (
                        job,
                        ingress_scope,
                        event_id,
                        fingerprint,
                        manifest_json,
                        _now(),
                        owner_client_id,
                    ),
                )
            else:
                if row["input_digest"] != fingerprint or row["owner_client_id"] != owner_client_id:
                    raise EventConflict("prepared_receipt_binding_changed")
                job = row["job_id"]
        row = _row(connection, job)
        if row["source_version_id"] is None:
            self._commit_version(connection, row, data, vault_id)
        else:
            self._verify_links(connection, row, data, vault_id)
        return _receipt(_row(connection, job))

    def _record_failure(self, connection: sqlite3.Connection, job_id: str, exc: Exception) -> None:
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            code = "disk_full"
        elif isinstance(exc, sqlite3.Error):
            code = "database_error"
        elif isinstance(exc, (IntegrityError, InvalidInput)):
            code = "integrity_error"
        else:
            code = "io_error"
        try:
            with database.transaction(connection):
                connection.execute(
                    "UPDATE receipts SET state='failed',error_code=? WHERE job_id=?", (code, job_id)
                )
        except (OSError, sqlite3.Error) as status_error:
            # The caller sees both failures; never imply a failure record was persisted.
            exc.add_note("failure status could not be persisted")
            raise exc from status_error

    def _finish(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        data: dict[str, Any],
        vault_id: str,
    ) -> Receipt:
        missing = False
        for asset in data["assets"]:
            relative = objects.object_relative(vault_id, asset["sha256"])
            try:
                from .object_streams import verify

                verify(
                    contained(self.config.state_root, relative),
                    asset["sha256"],
                    asset["byte_count"],
                )
            except FileNotFoundError:
                missing = True
        if missing:
            with database.transaction(connection):
                connection.execute(
                    "UPDATE receipts SET state='incomplete',error_code='missing_bytes' "
                    "WHERE job_id=?",
                    (row["job_id"],),
                )
            return _receipt(_row(connection, row["job_id"]))
        if row["source_version_id"] is not None:
            self._verify_links(connection, row, data, vault_id)
            with database.transaction(connection):
                connection.execute(
                    "UPDATE receipts SET state='stored',error_code=NULL WHERE job_id=?",
                    (row["job_id"],),
                )
            return _receipt(_row(connection, row["job_id"]))
        self._commit_version(connection, row, data, vault_id)
        return _receipt(_row(connection, row["job_id"]))

    def _commit_version(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        data: dict[str, Any],
        vault_id: str,
    ) -> None:
        with database.transaction(connection):
            source = connection.execute(
                "SELECT id FROM sources WHERE vault_id=? AND source_key=?",
                (vault_id, data["source_key"]),
            ).fetchone()
            source_id = source[0] if source else str(uuid4())
            if not source:
                connection.execute(
                    "INSERT INTO sources VALUES(?,?,?,?)",
                    (source_id, vault_id, data["source_key"], _now()),
                )
            version_digest = objects.digest(canonical(data["assets"][1:]).encode())
            version = connection.execute(
                "SELECT id,manifest_digest FROM source_versions WHERE source_id=? "
                "ORDER BY version_no DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            if version is not None and version["manifest_digest"] != version_digest:
                version = None
            version_id = version[0] if version else str(uuid4())
            if not version:
                number = connection.execute(
                    "SELECT coalesce(max(version_no),0)+1 FROM source_versions WHERE source_id=?",
                    (source_id,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO source_versions VALUES(?,?,?,?,?,?)",
                    (version_id, source_id, number, version_digest, data["retention"], _now()),
                )
            elif data["retention"] == "knowledge":
                connection.execute(
                    "UPDATE source_versions SET retention='knowledge' WHERE id=?", (version_id,)
                )
            ids = []
            for asset in data["assets"]:
                old = connection.execute(
                    "SELECT id FROM assets WHERE vault_id=? AND sha256=?",
                    (vault_id, asset["sha256"]),
                ).fetchone()
                asset_id = old[0] if old else str(uuid4())
                if not old:
                    connection.execute(
                        "INSERT INTO assets VALUES(?,?,?,?,?)",
                        (
                            asset_id,
                            vault_id,
                            asset["sha256"],
                            asset["byte_count"],
                            objects.object_relative(vault_id, asset["sha256"]),
                        ),
                    )
                ids.append(asset_id)
            if not version:
                for ordinal, (asset, asset_id) in enumerate(
                    zip(data["assets"][1:], ids[1:], strict=True)
                ):
                    connection.execute(
                        "INSERT INTO version_assets VALUES(?,?,?,?,?,?)",
                        (
                            version_id,
                            asset_id,
                            ordinal,
                            asset["role"],
                            asset["original_name"],
                            asset["media_type"],
                        ),
                    )
            connection.execute(
                "INSERT OR IGNORE INTO session_refs VALUES(?,?,?)",
                (data["session_scope"], data["session_id"], version_id),
            )
            connection.execute(
                "UPDATE receipts SET state='stored',source_version_id=?,raw_asset_id=?,"
                "error_code=NULL WHERE job_id=?",
                (version_id, ids[0], row["job_id"]),
            )
            self._verify_links(connection, _row(connection, row["job_id"]), data, vault_id)

    def _verify_links(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        data: dict[str, Any],
        vault_id: str,
    ) -> None:
        version = connection.execute(
            "SELECT s.vault_id,s.source_key,v.manifest_digest FROM source_versions v "
            "JOIN sources s ON s.id=v.source_id WHERE v.id=?",
            (row["source_version_id"],),
        ).fetchone()
        expected = objects.digest(canonical(data["assets"][1:]).encode())
        if version is None or tuple(version) != (vault_id, data["source_key"], expected):
            raise IntegrityError("version relationship mismatch")
        raw = connection.execute(
            "SELECT * FROM assets WHERE id=?", (row["raw_asset_id"],)
        ).fetchone()
        linked = connection.execute(
            "SELECT a.*,va.role,va.original_name,va.media_type,va.ordinal FROM version_assets va "
            "JOIN assets a ON a.id=va.asset_id WHERE va.version_id=? ORDER BY va.ordinal",
            (row["source_version_id"],),
        ).fetchall()
        if raw is None or len(linked) != len(data["assets"]) - 1:
            raise IntegrityError("asset relationship mismatch")
        for i, (actual, asset) in enumerate(zip([raw, *linked], data["assets"], strict=True)):
            if (
                actual["vault_id"],
                actual["sha256"],
                actual["byte_count"],
                actual["relative_path"],
            ) != (
                vault_id,
                asset["sha256"],
                asset["byte_count"],
                objects.object_relative(vault_id, asset["sha256"]),
            ):
                raise IntegrityError("asset metadata mismatch")
            if i and (
                actual["role"],
                actual["original_name"],
                actual["media_type"],
                actual["ordinal"],
            ) != (
                asset["role"],
                asset["original_name"],
                asset["media_type"],
                i - 1,
            ):
                raise IntegrityError("asset ordering or role mismatch")

    def verify_receipt_locked(self, connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
        """Validate stored receipt metadata; caller authorizes and holds the storage lock."""
        row = _row(connection, job_id)
        if row["state"] != "stored":
            raise IntegrityError("receipt is not stored")
        data = self._manifest(row)
        self._verify_links(connection, row, data, self._vault_id(connection, data["vault"]))
        return data

    def receipt(self, job_id: str) -> Receipt:
        text(job_id)
        with self._connection() as connection:
            row = _row(connection, job_id)
            self._manifest(row)
            return _receipt(row)

    def recover(self) -> list[Receipt]:
        """Reconcile each durable receipt; preserve partial/unreferenced bytes."""
        with self._connection() as connection:
            result = []
            for row in connection.execute("SELECT * FROM receipts ORDER BY created_at").fetchall():
                try:
                    data = self._manifest(row)
                    vault_id = self._vault_id(connection, data["vault"])
                    result.append(self._finish(connection, row, data, vault_id))
                except (OSError, sqlite3.Error, StorageError) as exc:
                    self._record_failure(connection, row["job_id"], exc)
                    result.append(_receipt(_row(connection, row["job_id"])))
            return result

    def read_asset(self, asset_id: str, *, vault: str) -> bytes:
        text(asset_id)
        with self._connection() as connection:
            vault_id = self._vault_id(connection, vault)
            row = connection.execute(
                "SELECT * FROM assets WHERE id=? AND vault_id=?", (asset_id, vault_id)
            ).fetchone()
            if row is None:
                raise InvalidInput("unknown asset in requested vault")
            expected = objects.object_relative(vault_id, row["sha256"])
            if row["relative_path"] != expected:
                raise IntegrityError("invalid object path in database")
            from .media_cleanup_store import cleaned

            if (
                cleaned(connection, asset_id)
                and not contained(self.config.state_root, expected).exists()
            ):
                raise InvalidInput("media_original_cleaned_after_publication")
            return objects.read_object(
                self.config.state_root, expected, row["sha256"], row["byte_count"]
            )

    def clear_session_refs(self, scope: str, session_id: str) -> int:
        """Low-level reference removal, not the later user-facing cleanup workflow."""
        text(scope)
        text(session_id)
        with self._connection() as connection, database.transaction(connection):
            return connection.execute(
                "DELETE FROM session_refs WHERE session_scope=? AND session_id=?",
                (scope, session_id),
            ).rowcount

    def snapshot(self) -> Path:
        """Metadata snapshot plus hash list; not a full portable system backup."""
        with self._connection() as connection:
            for row in connection.execute("SELECT * FROM receipts").fetchall():
                if row["state"] != "stored":
                    raise IntegrityError("reconcile incomplete receipts before snapshot")
                data = self._manifest(row)
                self._verify_links(connection, row, data, self._vault_id(connection, data["vault"]))
            listing = []
            for row in connection.execute("SELECT * FROM assets ORDER BY id").fetchall():
                expected = objects.object_relative(row["vault_id"], row["sha256"])
                if row["relative_path"] != expected:
                    raise IntegrityError("invalid snapshot object path")
                from .media_cleanup_store import cleaned

                removed = (
                    cleaned(connection, row["id"])
                    and not contained(self.config.state_root, expected).exists()
                )
                if not removed:
                    from .object_streams import verify

                    verify(
                        contained(self.config.state_root, expected),
                        row["sha256"],
                        row["byte_count"],
                    )
                listing.append(
                    {
                        **dict(row),
                        **({"retention_state": "cleaned_after_publication"} if removed else {}),
                    }
                )
            path = database.backup_database(connection, self.config.state_root)
            listing_path = contained(
                self.config.state_root,
                path.relative_to(self.config.state_root).as_posix() + ".json",
            )
            fd = os.open(listing_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            objects._write_bytes(
                fd,
                canonical(
                    {
                        "schema_version": database.SCHEMA_VERSION,
                        "database_sha256": objects.digest(path.read_bytes()),
                        "assets": listing,
                    }
                ).encode(),
            )
            sync_directory(path.parent)
            return path
