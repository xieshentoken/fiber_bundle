"""Read-only offline backup preflight. A PASS is not a backup or permission to restore."""

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from typing import Any

from . import database, media_cleanup_store, objects, vaults
from .backup_inventory import Inventory, Limits
from .backup_progress import Progress, SourceChanged, report
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical

DATABASES = {
    "supplier-intelligence.sqlite3": 1,
    "metadata.sqlite3": database.SCHEMA_VERSION,
    "weixin-inbox.sqlite3": 1,
    "document-creation.sqlite3": 1,
    "note-identities.sqlite3": 1,
    "note-migration-executions.sqlite3": 1,
    "moc-associations.sqlite3": 1,
    "organization.sqlite3": 1,
    "moc-dispatch.sqlite3": 1,
    "knowledge-graph.sqlite3": 1,
    "message-channels.sqlite3": 3,
}
STATE_DIRECTORIES = frozenset(
    {
        "objects",
        "agent-connections",
        "agent-expert-query",
        "vault-jobs",
        "source-imports",
        "source-expert",
        "expert-authoring",
        "expert-tool-snapshots",
        "project-skills",
        "video-skills",
        "video-skill-jobs",
        "skill-exports",
        "note-search",
        "staging",
        "snapshots",
        "tasks",
        "article-captures",
        "document-captures",
        "media-captures",
        "media-processing",
        "video-segments",
        "video-artifacts",
        "video-links",
        "media-links",
        "document-runs",
        "document-drafts",
        "local-query-tasks",
        "note-snapshots",
        "note-migrations",
        "note-transfer-files",
        "publication-transfer-files",
        "publication-origins",
        "layout-migration-backups",
        "vault-relocations",
        "moc-files",
        "moc-note-files",
        "maintenance-undo-blobs",
        "organization-execution",
        "channel-query-results",
        "query-policies",
        "configs",
        "model-secrets",
        "execution-locks",
        "graph-locks",
        "execution-queue",
        # Legacy deployment/acceptance archives are opaque preserved evidence, not live DBs.
        # Future deployment and test outputs belong outside state_root.
        "deployment-backups",
        "m4-acceptance",
        "m5-acceptance",
        "m6-acceptance",
        "m8-acceptance",
        "m8-runtime",
    }
)
STATE_FILES = frozenset(
    {
        "storage.lock",
        "intake-abort.lock",
        "backend.lock",
        "backend.json",
        "answer-worker.lock",
        "weixin-receiver.lock",
        "weixin-store-worker.lock",
        "weixin-task-worker.lock",
        "weixin-submission-worker.lock",
        "weixin-media-worker.lock",
        "weixin-execution-worker.lock",
        "query-policy.json",
        "knowledge-graph-policy.json",
        "query-runner-config.json",
        "restore-gate.json",
        "restore-rebind.json",
        "deployed-ui.json",  # Historical deployment evidence; never activates a restored service.
        ".DS_Store",  # Preserve existing system metadata rather than deleting or hiding it.
    }
)
EXCLUDED = frozenset(
    {"browser-profiles", "message-channel-secrets", "agent-connection-secrets", "runtime"}
)
DEFAULT_LIMITS = Limits()


def _paths(config: Config, limits: Limits) -> Iterator[tuple[str, os.stat_result]]:
    count = 0
    for parent, directories, files in os.walk(config.state_root, followlinks=False):
        if len(Path(parent).relative_to(config.state_root).parts) > limits.depth:
            raise InvalidInput("backup_depth_limit")
        if parent == str(config.state_root):
            directories[:] = [name for name in directories if name not in EXCLUDED]
        if Path(parent) == config.state_root / "configs":
            directories[:] = [name for name in directories if not name.startswith("hermes-feishu-")]
        for name in sorted([*directories, *files]):
            count += 1
            if count > limits.entries:
                raise InvalidInput("backup_entry_limit")
            relative = (Path(parent) / name).relative_to(config.state_root).as_posix()
            path = contained(config.state_root, relative)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) and not (
                stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            ):
                raise InvalidInput("backup_requires_independent_regular_files")
            yield relative, info


@contextmanager
def _offline(config: Config, limits: Limits) -> Iterator[None]:
    """Nonblocking locks only; never stop processes, create locks, or reclaim tickets."""
    with ExitStack() as stack:
        held: dict[str, int] = {}
        storage = contained(config.state_root, "storage.lock")
        fd = os.open(storage, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise InvalidInput("backup_lock_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("backup_storage_busy") from None
        held["storage.lock"] = fd
        from .channels.control import offline_ready

        if not offline_ready(config):
            raise InvalidInput("backup_channel_runtime_busy")
        # Application-native task owners may keep working outside the storage lock.
        for relative, info in _paths(config, limits):
            if relative == "storage.lock" or not (
                relative.endswith(".lock")
                or (relative.startswith("execution-locks/") and stat.S_ISREG(info.st_mode))
                or relative.endswith(".ticket")
            ):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise InvalidInput("backup_lock_invalid")
            safe = contained(config.state_root, relative)
            fd = os.open(safe, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InvalidInput("backup_runtime_busy") from None
            held[relative] = fd
        yield
        current = {"storage.lock"}
        for relative, info in _paths(config, limits):
            if relative.endswith((".lock", ".ticket")) or (
                relative.startswith("execution-locks/") and stat.S_ISREG(info.st_mode)
            ):
                current.add(relative)
        if current != set(held):
            raise IntegrityError("backup_runtime_changed")
        for relative, descriptor in held.items():
            path_info = contained(config.state_root, relative).lstat()
            lock_info = os.fstat(descriptor)
            if (path_info.st_dev, path_info.st_ino) != (lock_info.st_dev, lock_info.st_ino):
                raise IntegrityError("backup_runtime_changed")


def _state_scope(config: Config, *, maximum: int | None = None, online: bool = False) -> None:
    for count, path in enumerate(config.state_root.iterdir(), start=1):
        if maximum is not None and count > maximum:
            raise InvalidInput("backup_entry_limit")
        name = path.name
        if name in EXCLUDED:
            if path.is_symlink() or not path.is_dir():
                raise InvalidInput("backup_exclusion_invalid")
            continue
        contained(config.state_root, name)
        if name in STATE_DIRECTORIES and path.is_dir():
            continue
        if name in STATE_FILES or name in DATABASES:
            if not path.is_file():
                raise InvalidInput("backup_state_type_invalid")
            continue
        if any(name == db + suffix for db in DATABASES for suffix in ("-wal", "-shm", "-journal")):
            # An offline immutable reader must never ignore committed WAL or hot journals.
            if not path.is_file() or (path.stat().st_size and not online):
                raise InvalidInput("backup_database_requires_offline_checkpoint")
            continue
        raise InvalidInput("backup_unclassified_state")


def check_state_scope(config: Config) -> None:
    """Lightweight top-level classification only, safe while the backend holds its lock.

    Does not read database/Vault contents, acquire offline locks or certify a backup.
    Full offline validation still occurs at execution, including nested file safety.
    """
    _state_scope(config, maximum=DEFAULT_LIMITS.entries)


@contextmanager
def _database(
    config: Config,
    name: str,
    *,
    version: int | None = None,
    overrides: dict[str, Path] | None = None,
) -> Iterator[sqlite3.Connection]:
    path = (overrides or {}).get(name, contained(config.state_root, name))
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    try:
        expected = DATABASES[name] if version is None else version
        supported = (
            {1, 2, 3} if name == "message-channels.sqlite3" and version is None else {expected}
        )
        if db.execute("PRAGMA user_version").fetchone()[0] not in supported:
            raise IntegrityError("backup_unsupported_schema")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IntegrityError("backup_database_integrity")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise IntegrityError("backup_database_foreign_keys")
        yield db
    finally:
        db.close()


def _objects(db: sqlite3.Connection, inventory: Inventory) -> dict[str, int]:
    files = {e["path"]: e for e in inventory.entries if e["section"] == "state"}
    retained = removed = 0
    for row in db.execute("SELECT * FROM assets ORDER BY id"):
        expected = objects.object_relative(row["vault_id"], row["sha256"])
        if row["relative_path"] != expected:
            raise IntegrityError("backup_object_path_invalid")
        entry = files.get(expected)
        if entry is None and media_cleanup_store.cleaned(db, row["id"]):
            removed += 1
            continue
        if entry is None or (entry.get("sha256"), entry.get("bytes")) != (
            row["sha256"],
            row["byte_count"],
        ):
            raise IntegrityError("backup_object_missing_or_changed")
        retained += 1
    return {"retained": retained, "cleaned_after_publication": removed}


def _drafts(db: sqlite3.Connection, inventory: Inventory) -> int:
    files = {e["path"]: e for e in inventory.entries if e["section"] == "state"}
    count = 0
    for row in db.execute("SELECT * FROM document_creation_artifacts"):
        try:
            metadata = json.loads(row["artifact_json"])
            artifact = metadata["artifact"]
            entry = files[row["relative_path"]]
            if (
                objects.digest(row["artifact_json"].encode()) != row["artifact_sha256"]
                or (entry.get("sha256"), entry.get("bytes"))
                != (artifact["sha256"], artifact["byte_count"])
                or not row["relative_path"].startswith("document-drafts/" + row["run_id"] + "/")
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise IntegrityError("backup_draft_missing_or_changed") from exc
        count += 1
    return count


def _inventory_sources(
    config: Config,
    inventory: Inventory,
    *,
    online: bool,
    include_configs: tuple[str, ...] = (),
    live: bool = False,
    overrides: dict[str, Path] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    excluded = set(EXCLUDED)
    if online:
        excluded.update(transient_files(config))
    configs = config.state_root / "configs"
    if configs.is_dir():
        with os.scandir(configs) as children:
            excluded.update(
                "configs/" + child.name
                for child in children
                if child.name.startswith("hermes-feishu-")
            )
    if overrides:
        current = {name for name in DATABASES if (config.state_root / name).exists()}
        current.update(
            p.relative_to(config.state_root).as_posix()
            for p in (config.state_root / "note-search").glob("*.sqlite3")
        )
        if current != set(overrides):
            raise SourceChanged("state", "database_set")
    inventory.tree(config.state_root, "state", skip=frozenset(excluded))
    for relative in ("skills/fake-expert", "skills/bili-note-V3"):
        skill = contained(config.project_root, relative)
        if skill.exists():
            inventory.directory(config.project_root, relative, "configuration")
    if len(include_configs) != len(set(include_configs)) or len(include_configs) > 100:
        raise InvalidInput("backup_configuration_selection_invalid")
    for relative in include_configs:
        if not relative.startswith("configs/"):
            raise InvalidInput("backup_configuration_selection_invalid")
        inventory.file(config.project_root, relative, "configuration")
    reader = (
        closing(database.connect(config.state_root, readonly=True))
        if live
        else _database(config, "metadata.sqlite3", overrides=overrides)
    )
    with reader as db:
        registered = []
        from .vault_scope import for_vault

        scopes = {}
        for row in db.execute("SELECT * FROM vaults ORDER BY id"):
            if row["attachment_state"] == "detached" or row["attachment_state"] == "trashed":
                continue
            if row["attachment_state"] != "attached":
                raise InvalidInput("backup_vault_operation_unresolved")
            path = vaults.checked_vault(config, row, pending=True, metadata=db)
            scope = for_vault(path, row)
            scopes[str(path)] = scope
            inventory.tree(path, "vault:" + row["id"], scope=scope)
            registered.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "relative_path": row["relative_path"],
                    "status": row["status"],
                    "absolute_root": row["absolute_root"],
                    "root_kind": row["root_kind"],
                    "scope": scope.wire(),
                    "physical_identity_requires_rebind_on_restore": True,
                }
            )
    return registered, scopes


def capacity(
    config: Config,
    *,
    limits: Limits = DEFAULT_LIMITS,
    online: bool = True,
    progress: Progress | None = None,
) -> dict[str, int]:
    """Metadata-only limits before keys, write reservations or encrypted IO.

    This is an early bound, never a consistency certificate. The frozen pass
    repeats all safety checks and rejects growth or changes after this estimate.
    """
    inventory = Inventory(limits, metadata_only=True, progress=progress)
    _inventory_sources(config, inventory, online=online, live=True)
    result = {
        "files": inventory.file_count,
        "bytes": inventory.byte_count,
        "entries": len(inventory.entries),
    }
    report(
        progress, "capacity", files_done=result["files"], bytes_done=result["bytes"], complete=True
    )
    return result


@contextmanager
def frozen(
    config: Config,
    *,
    limits: Limits = DEFAULT_LIMITS,
    include_configs: tuple[str, ...] = (),
    _after_validation: Callable[[], None] | None = None,
    online: bool = False,
    inventory_cache: dict[str, Any] | None = None,
    captured_cache: dict[str, Any] | None = None,
    progress: Progress | None = None,
    source_overrides: dict[str, Path] | None = None,
) -> Iterator[dict[str, Any]]:
    """Freeze hashes of the observed scope; don't follow external credential references.

    All state remains sensitive and requires protected handling in a future backup.
    This manifest has no bytes needed for recovery and may become stale immediately.
    """
    config.validate()
    inventory = Inventory(limits, inventory_cache, progress=progress)
    from .backup_automation.online import snapshots

    if not online:
        _state_scope(config, online=True) if online else _state_scope(config)
    with snapshots(config, limits) if online else _offline(config, limits) as captured_databases:
        overrides = captured_databases or {}
        inventory.state_overrides = overrides
        if source_overrides is not None:
            source_overrides.update(overrides)
        _state_scope(config, online=True) if online else _state_scope(config)
        with _database(config, "metadata.sqlite3", overrides=overrides) as ledger:
            if ledger.execute(
                "SELECT 1 FROM agent_connection_operations "
                "WHERE state IN ('queued','running') LIMIT 1"
            ).fetchone():
                raise InvalidInput("backup_agent_operation_busy")
        registered, scopes = _inventory_sources(
            config, inventory, online=online, include_configs=include_configs, overrides=overrides
        )
        with _database(config, "metadata.sqlite3", overrides=overrides) as db:
            assets = _objects(db, inventory)
            drafts = _drafts(db, inventory)
            receipts = {
                row["state"]: row["n"]
                for row in db.execute("SELECT state,count(*) AS n FROM receipts GROUP BY state")
            }
        sidecars = []
        for name in DATABASES:
            if contained(config.state_root, name).exists():
                with _database(config, name, overrides=overrides) as sidecar:
                    sidecars.append(
                        {
                            "path": name,
                            "schema": sidecar.execute("PRAGMA user_version").fetchone()[0],
                        }
                    )
        index_root = contained(config.state_root, "note-search")
        if index_root.exists():
            from .note_changes import identifier
            from .note_search_jobs import read as read_index_job

            for path in sorted(index_root.glob("*.job.json")):
                vault_id = path.name.removesuffix(".job.json")
                identifier(vault_id)
                read_index_job(config, vault_id)

            for path in sorted(index_root.glob("*.sqlite3")):
                identifier(path.stem)
                relative = path.relative_to(config.state_root).as_posix()
                with _database(config, relative, version=1, overrides=overrides):
                    sidecars.append({"path": relative, "schema": 1})
        inventory.unchanged()
        _state_scope(config, online=True) if online else _state_scope(config)
        inventory.unchanged()
        value = {
            "schema": "backup-preflight/v1",
            "state": "PASS",
            "full_system_backup": False,
            "restorable": False,
            "valid_for": "observed_state_only",
            "storage_config": {"state_root": config.state_relative, "vaults": dict(config.vaults)},
            "vault_roots": dict(config.vault_roots),
            "vaults": registered,
            "databases": sidecars,
            "assets": assets,
            "document_drafts": drafts,
            "receipts": receipts,
            "entries": sorted(inventory.entries, key=lambda e: (e["section"], e["path"])),
            "file_bytes": inventory.byte_count,
            "secret_handling": "state_requires_separate_protection_or_reconfiguration",
            "excluded": [
                "browser-profiles",
                "message-channel-secrets",
                "agent-connection-secrets",
                "configs/hermes-feishu-*",
                "external_credentials",
                "external_configs",
                "application_and_dependencies",
                "runtime (pinned Hermes components; reinstall after restore)",
            ],
            "runtime_policy": "no_start_no_replay_no_migration",
        }
        if captured_cache is not None:
            captured_cache.update(
                files=inventory.captured,
                hashed_bytes=inventory.hashed_bytes,
                reused_bytes=inventory.reused_bytes,
            )
        report(
            progress,
            "scanning",
            files_done=inventory.file_count,
            bytes_done=inventory.byte_count,
            complete=True,
        )
        yield {**value, "sha256": hashlib.sha256(canonical(value).encode()).hexdigest()}
        inventory.unchanged()
        # Remote status can change without changing local content bytes.
        from .cloud_files import require_available

        for root in scopes:
            scope = scopes[root]
            require_available(Path(root), tree=not scope.exclude and scope.include == ("",))
        _state_scope(config, online=True) if online else _state_scope(config)
        # Only operational gate changes use this hook, before releasing offline ownership.
        if _after_validation is not None:
            _after_validation()


def inspect(config: Config, *, limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    with frozen(config, limits=limits) as value:
        return value


def recheck(
    config: Config, expected_sha256: str, *, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_sha256)
    ):
        raise InvalidInput("backup_digest_invalid")
    result = inspect(config, limits=limits)
    if result["sha256"] != expected_sha256:
        raise IntegrityError("backup_scope_changed")
    return result


def transient_files(config: Config) -> set[str]:
    """SQLite coordination files contain no extra commits after a successful checkpoint."""
    names = set(DATABASES)
    root = config.state_root / "note-search"
    if root.is_dir():
        names.update(p.relative_to(config.state_root).as_posix() for p in root.glob("*.sqlite3"))
    return {name + suffix for name in names for suffix in ("-wal", "-shm", "-journal")}
