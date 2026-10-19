"""Host-only opt-in snapshots and delivery guards. Worker receives no filesystem capabilities."""

import hashlib
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import uuid4

from .. import auth, database, note_provenance, note_search_inventory, search, vaults
from ..config import Config, IntegrityError, InvalidInput
from ..moc_store import stable_identity
from .contracts import MAX_NOTES, Engine, Request, Source, digest
from .results import Result, decode_result


@dataclass(frozen=True)
class Selection:
    note_id: str
    expected_sha256: str


@dataclass(frozen=True)
class Binding:
    note_id: str
    relative_path: str
    file_key: tuple[int, int, str]
    vault_key: tuple[int, int]


@dataclass(frozen=True)
class Prepared:
    request: Request
    bindings: tuple[Binding, ...]
    permission_epoch: int


def prepare(
    config: Config,
    authorization: str | None,
    vault_id: str,
    selected: tuple[Selection, ...],
    engine: Engine | None = None,
    *,
    enabled: bool = False,
    task_id: str | None = None,
    client_id: str | None = None,
    _locked: bool = False,
) -> Prepared:
    if enabled is not True:
        raise InvalidInput("knowledge_graph_disabled")
    if not 1 <= len(selected) <= MAX_NOTES or len({s.note_id for s in selected}) != len(selected):
        raise InvalidInput("graph_invalid_selection")
    for selection in selected:
        stable_identity(selection.note_id)
        digest(selection.expected_sha256)
    engine = engine or Engine()
    engine.validate()
    with nullcontext() if _locked else database.locked(config):
        db = database.connect(config.state_root)
        try:
            if authorization is not None:
                client_id = auth.client(db, authorization)["id"]
            elif (
                client_id is None
                or not db.execute(
                    "SELECT 1 FROM clients WHERE id=? AND active=1", (client_id,)
                ).fetchone()
            ):
                raise auth.Denied()
            vault = search.scope(db, client_id, [vault_id])[0]
            inventory = note_search_inventory.read(
                config, db, vault, selected={s.note_id for s in selected}
            )
            root = inventory.root.stat()
            notes = {note.identity: note for note in inventory.notes if note.identity is not None}
            sources, bindings = [], []
            for selection in sorted(selected, key=lambda s: s.note_id):
                note = notes.get(selection.note_id)
                if note is None or note.file_key is None:
                    raise IntegrityError("graph_selected_identity_unavailable")
                if note.sha256 != selection.expected_sha256:
                    raise IntegrityError("graph_selected_version_changed")
                provenance = note_provenance.describe(config, db, client_id, vault, inventory, note)
                sources.append(
                    Source(
                        selection.note_id,
                        note.sha256,
                        hashlib.sha256(note.text.encode()).hexdigest(),
                        note.title,
                        note.text,
                        provenance["source_kind"],
                        provenance["source_version_id"],
                    )
                )
                bindings.append(
                    Binding(
                        selection.note_id,
                        note.relative_path,
                        note.file_key,
                        (root.st_dev, root.st_ino),
                    )
                )
            # Another bounded observation catches races without writing identity/snapshot ledgers.
            current = note_search_inventory.read(
                config,
                db,
                auth.grant(db, client_id, vault_id, "query"),
                selected={s.note_id for s in selected},
            )
            by_id = {note.identity: note for note in current.notes}
            for binding, source in zip(bindings, sources, strict=True):
                note = by_id.get(binding.note_id)
                if note is None or (note.sha256, note.file_key, note.relative_path) != (
                    source.sha256,
                    binding.file_key,
                    binding.relative_path,
                ):
                    raise IntegrityError("graph_snapshot_changed")
            info = vaults.checked_vault(config, auth.grant(db, client_id, vault_id, "query")).stat()
            if (info.st_dev, info.st_ino) != (root.st_dev, root.st_ino):
                raise IntegrityError("graph_vault_identity_changed")
            request = Request(task_id or str(uuid4()), client_id, vault_id, engine, tuple(sources))
            request.encode()
            return Prepared(request, tuple(bindings), epoch(db, client_id, vault_id))
        finally:
            db.close()


def revalidate(
    config: Config,
    authorization: str | None,
    prepared: Prepared,
    *,
    enabled: bool = False,
    allow_rename: bool = False,
    _locked: bool = False,
) -> None:
    request = prepared.request
    current = prepare(
        config,
        authorization,
        request.vault_id,
        tuple(Selection(note.note_id, note.sha256) for note in request.notes),
        request.engine,
        enabled=enabled,
        task_id=request.task_id,
        client_id=request.client_id,
        _locked=_locked,
    )
    if allow_rename:
        current = Prepared(
            current.request,
            tuple(
                Binding(b.note_id, old.relative_path, b.file_key, b.vault_key)
                for b, old in zip(current.bindings, prepared.bindings, strict=True)
            ),
            current.permission_epoch,
        )
    if current != prepared:
        raise IntegrityError("graph_snapshot_or_authorization_changed")


def deliver(
    config: Config,
    authorization: str | None,
    prepared: Prepared,
    raw: bytes,
    *,
    enabled: bool = False,
) -> Result:
    result = decode_result(raw, prepared.request)
    revalidate(config, authorization, prepared, enabled=enabled)
    return result


def epoch(db: sqlite3.Connection, client_id: str, vault_id: str) -> int:
    row = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client_id, vault_id),
    ).fetchone()
    if row is None:
        raise IntegrityError("graph_authorization_epoch_unavailable")
    return int(row[0])
