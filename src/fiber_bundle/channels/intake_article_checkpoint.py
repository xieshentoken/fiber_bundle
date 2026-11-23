"""Private, disposable checkpoints for literal WeChat article downloads."""

import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .. import articles
from ..article_fetch import Response
from ..config import InvalidInput, contained, make_directory, sync_directory
from ..inputs import canonical
from ..objects import digest
from .intake_links import normalize

_FORMAT = "wechat-article-acquisition-v1"  # Bump when the parser's image binding changes.
_RETENTION = 7 * 24 * 60 * 60
_TASKS = 8
_METADATA_LIMIT = 64 * 1024
_IMAGE_RECORDS = 1024
_TASK_BYTES = articles.MAX_HTML_BYTES + articles.MAX_TOTAL_IMAGE_BYTES + 1024 * 1024
_DATABASE_BYTES = 768 * 1024 * 1024
_DATABASE = "runtime/intake-article-cache/checkpoints.sqlite3"
_OPTIONAL_CACHE_ERRORS = frozenset(
    {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_FULL}
)
_SCHEMA = """CREATE TABLE IF NOT EXISTS responses (
    scope TEXT NOT NULL, resource TEXT NOT NULL, source_version TEXT NOT NULL,
    metadata TEXT NOT NULL, body BLOB NOT NULL, integrity TEXT NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (scope, resource)
) STRICT"""


def _metadata(response: Response) -> str:
    return canonical([response.url, response.content_type, response.redirects])


class ArticleCheckpoint:
    """Retain a verified source and only the images belonging to its exact version."""

    def __init__(
        self,
        state_root: Path,
        *,
        identity: str,
        batch: str,
        index: int,
        revision: int,
        target_vault_id: str,
        source: str,
    ) -> None:
        self._root = state_root
        self._scope = digest(
            canonical(
                [_FORMAT, identity, batch, index, revision, target_vault_id, normalize(source)]
            ).encode()
        )
        self._source: tuple[Response, str] | None = None

    @contextmanager
    def _opened(self, *, create: bool = False) -> Iterator[sqlite3.Connection | None]:
        try:
            connection = self._connect(create)
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in _OPTIONAL_CACHE_ERRORS:
                raise InvalidInput("intake_checkpoint_unavailable") from None
            connection = None
        except (OSError, InvalidInput):
            raise InvalidInput("intake_checkpoint_unavailable") from None
        try:
            yield connection
        except sqlite3.Error as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in _OPTIONAL_CACHE_ERRORS:
                raise InvalidInput("intake_checkpoint_unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    def _connect(self, create: bool) -> sqlite3.Connection | None:
        path = contained(self._root, _DATABASE)
        if create and not path.exists():
            try:
                make_directory(path.parent)
            except FileExistsError:
                make_directory(path.parent)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass  # Another capture already created the same private cache.
            else:
                os.close(fd)
                sync_directory(path.parent)
        if not path.exists():
            return None
        info = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or path.parent.stat().st_mode & 0o077
        ):
            raise InvalidInput("intake_checkpoint_unavailable")
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
        try:
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute("PRAGMA fullfsync=ON")
            connection.execute("PRAGMA secure_delete=FAST")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={_DATABASE_BYTES // page_size}")
            if create:
                connection.execute("PRAGMA auto_vacuum=FULL")
                connection.execute(_SCHEMA)
            elif (
                connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='responses'"
                ).fetchone()
                is None
            ):
                connection.close()
                return None  # The database file was created before a crash.
        except BaseException:
            connection.close()
            raise
        return connection

    def _version(self, source: Response) -> str:
        if self._source is None or self._source[0] is not source:
            version = digest(canonical([_metadata(source), digest(source.data)]).encode())
            self._source = (source, version)
        return self._source[1]

    def _integrity(self, resource: str, version: str, metadata: str, data: bytes) -> str:
        return digest(canonical([self._scope, resource, version, metadata, digest(data)]).encode())

    def _load(self, resource: str, limit: int, version: str | None = None) -> Response | None:
        row: tuple[str, str, bytes, str] | None = None
        with self._opened() as connection:
            if connection is None:
                return None
            row = connection.execute(
                """SELECT r.source_version, r.metadata, r.body, r.integrity
                   FROM responses r JOIN responses s
                     ON s.scope=r.scope AND s.resource='' AND s.source_version=r.source_version
                   WHERE r.scope=? AND r.resource=? AND s.updated>=?
                     AND (? IS NULL OR s.source_version=?)
                     AND length(CAST(r.metadata AS BLOB))<=?
                     AND length(r.body) BETWEEN 1 AND ?""",
                (
                    self._scope,
                    resource,
                    time.time() - _RETENTION,
                    version,
                    version,
                    _METADATA_LIMIT,
                    limit,
                ),
            ).fetchone()
        return self._verified(resource, row) if row is not None else None

    def _verified(self, resource: str, row: tuple[str, str, bytes, str]) -> Response | None:
        stored_version, metadata, data, integrity = row
        if self._integrity(resource, stored_version, metadata, data) != integrity:
            return None
        try:
            url, content_type, redirects = json.loads(metadata)
        except (ValueError, TypeError):
            return None
        if (
            not isinstance(url, str)
            or not isinstance(content_type, str)
            or not isinstance(redirects, list)
            or len(redirects) > 6
            or any(not isinstance(hop, str) for hop in redirects)
        ):
            return None
        response = Response(url, content_type, data, tuple(redirects))
        if not resource and self._version(response) != stored_version:
            return None
        return response

    def load_source(self) -> Response | None:
        return self._load("", articles.MAX_HTML_BYTES)

    def load_image(self, source: Response, url: str) -> Response | None:
        return self._load(digest(url.encode()), articles.MAX_IMAGE_BYTES, self._version(source))

    def _save(
        self,
        connection: sqlite3.Connection,
        resource: str,
        version: str,
        response: Response,
        metadata: str,
        now: float,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self._scope,
                resource,
                version,
                metadata,
                response.data,
                self._integrity(resource, version, metadata, response.data),
                now,
            ),
        )

    def save_source(self, response: Response) -> None:
        metadata = _metadata(response)
        if not 0 < len(response.data) <= articles.MAX_HTML_BYTES or (
            len(metadata.encode()) > _METADATA_LIMIT
        ):
            return
        version = self._version(response)
        now = time.time()
        with self._opened(create=True) as connection:
            if connection is None:
                return
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """DELETE FROM responses WHERE scope IN (
                        SELECT scope FROM responses WHERE resource='' AND updated<?
                    ) OR (scope=? AND source_version<>?)""",
                    (now - _RETENTION, self._scope, version),
                )
                connection.execute(
                    """DELETE FROM responses WHERE scope IN (
                        SELECT scope FROM responses WHERE resource='' AND scope<>?
                        ORDER BY updated DESC, scope LIMIT -1 OFFSET ?
                    )""",
                    (self._scope, _TASKS - 1),
                )
                self._save(connection, "", version, response, metadata, now)

    def save_image(self, source: Response, url: str, response: Response) -> None:
        """The caller checks current ownership and validates format before saving."""
        metadata = _metadata(response)
        metadata_bytes = len(metadata.encode())
        if not 128 <= len(response.data) <= articles.MAX_IMAGE_BYTES or (
            metadata_bytes > _METADATA_LIMIT
        ):
            return
        version = self._version(source)
        resource = digest(url.encode())
        now = time.time()
        with self._opened() as connection:
            if connection is None:
                return
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                count, saved_bytes = connection.execute(
                    """SELECT count(*), coalesce(sum(length(body)+length(CAST(metadata AS BLOB))),0)
                       FROM responses WHERE scope=? AND source_version=? AND resource<>?
                        AND EXISTS (
                            SELECT 1 FROM responses
                            WHERE scope=? AND resource='' AND source_version=? AND updated>=?
                        )""",
                    (self._scope, version, resource, self._scope, version, now - _RETENTION),
                ).fetchone()
                if not 1 <= count <= _IMAGE_RECORDS or (
                    saved_bytes + len(response.data) + metadata_bytes > _TASK_BYTES
                ):
                    return
                self._save(connection, resource, version, response, metadata, now)
                connection.execute(
                    "UPDATE responses SET updated=? WHERE scope=? AND resource=''",
                    (now, self._scope),
                )

    def clear(self) -> None:
        if self._source is None:
            return
        with self._opened() as connection:
            if connection is not None:
                with connection:
                    connection.execute(
                        "DELETE FROM responses WHERE scope=? AND source_version=?",
                        (self._scope, self._source[1]),
                    )
