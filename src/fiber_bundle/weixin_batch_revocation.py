"""Explicit cancellation cleanup: hide only proven new, unchanged, unshared batch notes."""

import hashlib
import os
import sqlite3
from typing import Any

from . import (
    auth,
    batch_moc_contributions,
    database,
    file_exchange,
    moc_projection,
    note_inventory,
    note_migration,
    publication_origins,
    vaults,
    weixin_batches,
    weixin_inbox,
    weixin_publication,
    weixin_revocation_guard,
)
from . import (
    weixin_revocation_store as store,
)
from .config import Config, IntegrityError, InvalidInput
from .weixin_events import Binding


def authorize(config: Config, db: sqlite3.Connection, binding: Binding) -> sqlite3.Row:
    auth.grant(db, binding.client_id, binding.vault_id, "add")
    row = auth.grant(db, binding.client_id, binding.vault_id, "query")
    vaults.checked_vault(config, row)
    return row


def inspect(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    batch: str,
) -> list[dict[str, Any]]:
    row = authorize(config, db, binding)
    runs = weixin_publication.planned_locked(journal, binding, batch)
    inventory = note_inventory.read(config, db, row)
    mocs = note_migration.associations(config, binding.vault_id, inventory)
    candidates = (
        db.execute(
            "SELECT DISTINCT p.* FROM article_publications p JOIN article_publication_runs r "
            "ON r.publication_id=p.id WHERE r.run_id IN (" + ",".join("?" for _ in runs) + ")",
            runs,
        ).fetchall()
        if runs
        else []
    )
    result = []
    for pub in candidates:
        note = next((n for n in inventory.notes if n.publication_id == pub["id"]), None)
        origin = publication_origins.load(config, pub["id"])
        consumers = {
            r[0]
            for r in db.execute(
                "SELECT run_id FROM article_publication_runs WHERE publication_id=?", (pub["id"],)
            )
        }
        identity = "publication:" + pub["id"]
        contributions, protected = batch_moc_contributions.freeze(
            config, binding.vault_id, identity, runs, mocs
        )
        reason = "eligible"
        if pub["vault_id"] != binding.vault_id or pub["state"] != "published":
            reason = "location_or_state_changed"
        elif (
            origin is None or origin["run_id"] not in runs or origin["vault_id"] != binding.vault_id
        ):
            reason = "existing_or_unproven"
        elif consumers - set(runs) or protected:
            reason = "shared_or_associated"
        elif note is None:
            reason = "already_missing"
        elif (
            note.sha256 != origin["sha256"]
            or list(note.file_key or ()) != origin["file_key"]
            or hashlib.sha256(pub["manifest_json"].encode()).hexdigest()
            != origin["manifest_sha256"]
        ):
            reason = "human_modified_or_replaced"
        result.append(
            {
                "publication_id": pub["id"],
                "moc_changes": contributions if reason != "location_or_state_changed" else [],
                "reason": reason,
                "note_path": note.relative_path if note else None,
                "sha256": note.sha256 if note else None,
                "file_key": list(note.file_key) if note and note.file_key else None,
                "vault_key": [inventory.root.stat().st_dev, inventory.root.stat().st_ino],
                "parent_key": [
                    (inventory.root / note.relative_path).parent.stat().st_dev,
                    (inventory.root / note.relative_path).parent.stat().st_ino,
                ]
                if note
                else None,
            }
        )
    return sorted(result, key=lambda r: r["publication_id"])


def complete(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    batch: str,
    item: dict[str, Any],
    *,
    move: bool,
) -> str:
    row = authorize(config, db, binding)
    root = vaults.checked_vault(config, row)
    info = root.stat()
    if [info.st_dev, info.st_ino] != item["vault_key"]:
        raise IntegrityError("revocation Vault changed")
    relative = item["note_path"]
    fd = file_exchange.parent_fd(root, relative)
    name = relative.rsplit("/", 1)[-1]
    hidden = ".fiber-revoked-" + item["publication_id"] + ".md"
    try:
        parent = os.fstat(fd)
        if [parent.st_dev, parent.st_ino] != item["parent_key"]:
            raise IntegrityError("revocation parent changed")

        def guard() -> None:
            current_root = weixin_revocation_guard.check(config, journal, db, binding, batch, item)
            fresh = file_exchange.parent_fd(current_root, relative)
            try:
                info = os.fstat(fresh)
                if [info.st_dev, info.st_ino] != item["parent_key"]:
                    raise IntegrityError("revocation parent replaced")
            finally:
                os.close(fresh)

        guard()

        def matches(filename: str) -> bool:
            try:
                data, key = file_exchange.read(fd, filename)
            except FileNotFoundError:
                return False
            info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
            birth = getattr(info, "st_birthtime", None)
            full_key = [*key, float(birth if birth is not None else 0).hex()]
            if full_key != item["file_key"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise IntegrityError("revocation physical file changed; preserve both files")
            return True

        source, target = matches(name), matches(hidden)
        if move:
            if not source or target:
                raise IntegrityError("revocation file locations conflict")
            current = inspect(config, journal, db, binding, batch)
            latest = next(
                (v for v in current if v["publication_id"] == item["publication_id"]), None
            )
            if latest is None or {k: v for k, v in latest.items() if k != "moc_changes"} != {
                k: v for k, v in item.items() if k != "moc_changes"
            }:
                raise IntegrityError("revocation eligibility changed")
            guard()
            file_exchange.rename(fd, name, fd, hidden, exchange=False)
        elif source and not target:
            return "not_revoked"
        if matches(name) or not matches(hidden):
            raise IntegrityError("revocation file effect unresolved")
        guard()
        batch_moc_contributions.apply(
            config, binding.vault_id, binding.client_id, batch, item, guard=guard
        )
        guard()
        if matches(name) or not matches(hidden):
            raise IntegrityError("revocation physical evidence changed after MOC commit")
        return "revoked"
    finally:
        os.close(fd)


def complete_links(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    batch: str,
    item: dict[str, Any],
) -> str:
    def guard() -> None:
        weixin_revocation_guard.check(config, journal, db, binding, batch, item, links_only=True)

    guard()
    batch_moc_contributions.apply(
        config, binding.vault_id, binding.client_id, batch, item, guard=guard, links_only=True
    )
    guard()
    return "links_revoked"


def run(config: Config, binding: Binding, batch: str, action: str = "plan") -> dict[str, Any]:
    if action not in {"plan", "apply", "reconcile", "status", "project-mocs"}:
        raise InvalidInput("invalid batch revocation action")
    with weixin_inbox.connection(config, binding) as journal:
        db = database.connect(config.state_root)
        try:
            authorize(config, db, binding)
            view = weixin_batches.snapshot(journal, binding, batch)
            if view["state"] != "cancelled":
                raise InvalidInput("explicit batch cancellation is required before revocation")
            if action == "plan":
                values = inspect(config, journal, db, binding, batch)
                if inspect(config, journal, db, binding, batch) != values:
                    raise IntegrityError("revocation observation changed")
                authorize(config, db, binding)
                store.save(journal, binding, batch, batch_moc_contributions.sequence(values))
            results = []
            projections = []
            projection_revisions: dict[str, int] = {}
            saved = store.load(journal, binding, batch)
            if action == "project-mocs" and any(
                phase in {"claimed", "links_claimed"} for _, phase in saved
            ):
                raise IntegrityError("reconcile revocation before projecting MOCs")
            for item, phase in saved:
                if action == "apply" and phase in {"claimed", "links_claimed"}:
                    raise IntegrityError("revocation claim requires explicit reconciliation")
                if action == "apply" and phase == "planned":
                    latest = next(
                        (
                            v
                            for v in inspect(config, journal, db, binding, batch)
                            if v["publication_id"] == item["publication_id"]
                        ),
                        None,
                    )
                    matches = (
                        item["reason"] == "eligible"
                        and latest is not None
                        and {k: v for k, v in latest.items() if k != "moc_changes"}
                        == {k: v for k, v in item.items() if k != "moc_changes"}
                    )
                    if matches:
                        try:
                            batch_moc_contributions.check(config, binding.vault_id, item)
                        except IntegrityError:
                            matches = False
                    if not matches:
                        if item.get("moc_changes"):
                            # File eligibility and link contribution are independent. A late
                            # edit protects the note without transferring ownership of links.
                            weixin_revocation_guard.check(
                                config, journal, db, binding, batch, item, links_only=True
                            )
                            store.transition(
                                journal, batch, item["publication_id"], phase, "links_claimed"
                            )
                            phase = "links_claimed"
                        else:
                            after = (
                                latest["reason"]
                                if latest and latest["reason"] != "eligible"
                                else "evidence_changed"
                            )
                            store.transition(journal, batch, item["publication_id"], phase, after)
                            results.append(
                                {"publication_id": item["publication_id"], "state": after}
                            )
                            continue
                    else:
                        store.transition(journal, batch, item["publication_id"], phase, "claimed")
                        phase = "claimed"
                if phase == "links_claimed" and action in {"apply", "reconcile"}:
                    after = complete_links(config, journal, db, binding, batch, item)
                    store.transition(journal, batch, item["publication_id"], phase, after)
                    phase = after
                if phase == "claimed" and action in {"apply", "reconcile"}:
                    after = complete(
                        config, journal, db, binding, batch, item, move=action == "apply"
                    )
                    store.transition(journal, batch, item["publication_id"], phase, after)
                    phase = after
                results.append(
                    {
                        "publication_id": item["publication_id"],
                        "state": phase,
                        **(
                            {
                                "note_retained": True,
                                "note_reason": item["reason"]
                                if item["reason"] != "eligible"
                                else "changed_after_plan",
                            }
                            if phase == "links_revoked"
                            else {}
                        ),
                    }
                )
                if phase in {"revoked", "links_revoked"}:
                    for change in item.get("moc_changes", []):
                        mid = change["moc_id"]
                        projection_revisions[mid] = max(
                            projection_revisions.get(mid, 0), change["after"]["revision"]
                        )
            if action == "project-mocs":
                for mid, revision in sorted(projection_revisions.items()):
                    try:
                        outcome = moc_projection.refresh(
                            config, db, binding.client_id, binding.vault_id, mid, revision
                        )
                    except (IntegrityError, OSError, ValueError, sqlite3.Error):
                        outcome = {"status": "conflict_preserved"}
                    projections.append({"moc_id": mid, **outcome})
            authorize(config, db, binding)
            return {
                "batch_id": batch,
                "results": results,
                "originals_retained": True,
                "attachments_retained": True,
                "external_effects_reversed": False,
                "automatic_replay": False,
                "moc_projections": projections,
            }
        finally:
            db.close()
