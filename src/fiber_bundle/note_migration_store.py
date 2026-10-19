"""Private execution intents and durable claims, separate from read-only preflights."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

from . import database, note_identities, note_identity_store
from . import note_migration_plans as plans
from .answer_model_output import reject_constant, unique_object
from .config import Config, IntegrityError, contained, sync_directory
from .inputs import canonical

SCHEMA = """
CREATE TABLE intents (
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
 intent_json TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN
 ('prepared','claimed','completed','aborted'))
) STRICT;
CREATE TABLE events (
 id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL, phase TEXT NOT NULL
) STRICT;
"""
MAX_INTENTS = 1000


@contextmanager
def connection(config: Config, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
    path = contained(config.state_root, "note-migration-executions.sqlite3")
    for suffix in ("-journal", "-wal", "-shm"):
        contained(config.state_root, path.name + suffix)
    new = write and not path.exists()
    if not write and not path.exists():
        yield None
        return
    if new:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        sync_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024 * 1024:
            raise IntegrityError("migration execution ledger is not a bounded independent file")
    finally:
        os.close(fd)
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, isolation_level=None
    )
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
            db.execute("PRAGMA fullfsync=ON")
        if new:
            with database.transaction(db):
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("PRAGMA user_version=1")
        elif db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("unsupported migration execution ledger")
        yield db
    finally:
        db.close()


def load(
    config: Config, client: str, source: str, target: str, execution: str
) -> tuple[dict[str, Any], str]:
    plans.path_for(config, client, source, target, execution)  # Validate identifiers only.
    with connection(config) as db:
        row = (
            None
            if db is None
            else db.execute(
                "SELECT * FROM intents WHERE id=? AND client_id=? AND source=? AND target=?",
                (execution, client, source, target),
            ).fetchone()
        )
        phases = (
            []
            if db is None
            else [
                r[0]
                for r in db.execute(
                    "SELECT phase FROM events WHERE intent_id=? ORDER BY id LIMIT 4", (execution,)
                )
            ]
        )
    if row is None:
        raise IntegrityError("migration execution unavailable")
    if (
        phases
        not in (
            ["prepared"],
            ["prepared", "claimed"],
            ["prepared", "claimed", "completed"],
            ["prepared", "claimed", "aborted"],
        )
        or phases[-1] != row["phase"]
    ):
        raise IntegrityError("migration execution phase audit changed")
    raw = row["intent_json"]
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if (
            len(raw.encode()) > 2 * 1024 * 1024
            or not isinstance(value, dict)
            or type(value.get("schema")) is not int
            or value["schema"] not in {2, 3, 4}
            or canonical(value) != raw
            or plans.digest(value) != execution
        ):
            raise ValueError("invalid execution")
        if value["schema"] == 4:
            from . import publication_transfer_intent

            publication_transfer_intent.validate_plan(value["plan"], client, source, target)
        else:
            plans.validate(value["plan"], client, source, target)
        if plans.digest(value["plan"]) != value["plan_id"]:
            raise ValueError("unsupported execution scope")
        registration(value)
        fields = {"schema", "action", "plan_id", "plan", "registration"}
        if value["schema"] == 2:
            if (
                set(value) != fields
                or value["action"] != "move_plain_note"
                or value["plan"]["review_flags"]
            ):
                raise ValueError("invalid plain execution")
        elif value["schema"] == 3:
            from . import note_transfer_intent

            if (
                set(value) != fields | {"request_id", "transfer"}
                or value["action"] != "transfer_reviewed_note"
            ):
                raise ValueError("invalid reviewed execution")
            note_transfer_intent.validate(value)
        else:
            if (
                set(value) != fields | {"request_id", "transfer"}
                or value["action"] != "transfer_publication"
            ):
                raise ValueError("invalid publication transfer execution")
            publication_transfer_intent.validate(value)
    except (ValueError, TypeError, RecursionError, KeyError) as exc:
        raise IntegrityError("invalid migration execution intent") from exc
    if row["phase"] not in {"prepared", "claimed", "completed", "aborted"}:
        raise IntegrityError("invalid migration execution phase")
    return value, row["phase"]


def save(config: Config, plan_id: str, plan: dict[str, Any]) -> str:
    before = next(
        (
            r
            for r in note_identity_store.records(config, plan["source_vault_id"])
            if r.identity == plan["note_identity"]
        ),
        None,
    )
    if before is None or plans.digest(asdict(before)) != plan["registration_sha256"]:
        raise IntegrityError("migration source registration changed")
    value = {
        "schema": 2,
        "action": "move_plain_note",
        "plan_id": plan_id,
        "plan": plan,
        "registration": asdict(before),
    }
    return save_value(config, value)


def save_value(config: Config, value: dict[str, Any]) -> str:
    plan = value["plan"]
    if value["schema"] == 4:
        from . import publication_transfer_intent

        publication_transfer_intent.validate(value)
    else:
        plans.validate(plan, plan["client_id"], plan["source_vault_id"], plan["target_vault_id"])
    registration(value)
    if len(canonical(value).encode()) > 2 * 1024 * 1024:
        raise IntegrityError("migration execution metadata limit exceeded")
    if value["schema"] == 3:
        from . import note_transfer_intent

        note_transfer_intent.validate(value)
    execution = plans.digest(value)
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            if db.execute("SELECT 1 FROM intents WHERE id=?", (execution,)).fetchone() is None:
                if db.execute("SELECT count(*) FROM intents").fetchone()[0] >= MAX_INTENTS:
                    raise IntegrityError("migration execution limit exceeded")
                db.execute(
                    "INSERT INTO intents VALUES(?,?,?,?,?,'prepared')",
                    (
                        execution,
                        plan["client_id"],
                        plan["source_vault_id"],
                        plan["target_vault_id"],
                        canonical(value),
                    ),
                )
                db.execute("INSERT INTO events(intent_id,phase) VALUES(?,'prepared')", (execution,))
    load(config, plan["client_id"], plan["source_vault_id"], plan["target_vault_id"], execution)
    return execution


def registration(value: dict[str, Any]) -> note_identity_store.Registration:
    raw, plan = value["registration"], value["plan"]
    if (
        not isinstance(raw, dict)
        or set(raw)
        != {
            "identity",
            "publication_id",
            "relative_path",
            "sha256",
            "file_key",
            "vault_key",
            "revision",
        }
        or plans.digest(raw) != plan["registration_sha256"]
    ):
        raise IntegrityError("invalid migration original registration")
    try:
        record = note_identity_store.Registration(
            **{**raw, "file_key": tuple(raw["file_key"]), "vault_key": tuple(raw["vault_key"])}
        )
        note_identities.validate(record)
        if (
            record.publication_id != (plan["publication"]["id"] if value["schema"] == 4 else None)
            or record.identity != plan["note_identity"]
            or record.revision != plan["revision"]
            or list(record.file_key) != plan["source_file_key"]
            or list(record.vault_key) != plan["source_vault_key"]
        ):
            raise ValueError("invalid registration scope")
        return record
    except (ValueError, TypeError) as exc:
        raise IntegrityError("invalid migration original registration") from exc


def transition(config: Config, execution: str, before: str, after: str) -> None:
    if (before, after) not in {
        ("prepared", "claimed"),
        ("claimed", "completed"),
        ("claimed", "aborted"),
    }:
        raise IntegrityError("invalid migration phase transition")
    with connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            if (
                db.execute(
                    "UPDATE intents SET phase=? WHERE id=? AND phase=?", (after, execution, before)
                ).rowcount
                != 1
            ):
                raise IntegrityError("migration execution phase conflict")
            db.execute("INSERT INTO events(intent_id,phase) VALUES(?,?)", (execution, after))


def guard(config: Config, vault: str) -> None:
    """A partial physical/identity transfer must never surface as two unrelated notes."""
    with connection(config) as db:
        if (
            db is not None
            and db.execute(
                "SELECT 1 FROM intents i WHERE (source=? OR target=?) AND (phase NOT IN "
                "('prepared','claimed','completed','aborted') OR phase IS NOT "
                "(SELECT phase FROM events e WHERE e.intent_id=i.id ORDER BY id DESC LIMIT 1)) "
                "LIMIT 1",
                (vault, vault),
            ).fetchone()
        ):
            raise IntegrityError("Vault migration phase audit changed")
        if (
            db is not None
            and db.execute(
                "SELECT 1 FROM intents WHERE phase='claimed' AND (source=? OR target=?) LIMIT 1",
                (vault, vault),
            ).fetchone()
        ):
            raise IntegrityError(
                "Vault has an unresolved migration; explicit reconciliation required"
            )
