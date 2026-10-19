"""Idempotent current-location, identity and MOC copies; historical source ledgers stay intact."""

import base64
import json
from pathlib import Path
from typing import Any

from . import (
    moc_store,
    note_identity_store,
    publication_file_io,
    publication_locations,
    vault_directories,
)
from .config import Config, IntegrityError
from .inputs import canonical
from .objects import digest


def apply(config: Config, db: Any, identity: str, value: dict[str, Any]) -> None:
    plan = value["plan"]
    selection = plan["selection"]
    target = selection["target_vault_id"]
    client = selection["client_id"]
    root = Path(plan["inventories"][-1]["context"]["root"])
    root_key = vault_directories.key(root)
    policy = json.loads(
        db.execute("SELECT value FROM vault_save_policies WHERE vault_id=?", (target,)).fetchone()[
            0
        ]
    )
    from .vault_merge_journal import save

    for source in plan["inventories"][:-1]:
        vid = source["context"]["vault_id"]
        moves = {
            f["source_path"]: f["target_path"] for f in plan["files"] if f["source_vault_id"] == vid
        }
        for pub in source["publications"]:
            old = publication_locations.get(db, pub["kind"], pub["id"])
            if old and old["plan"].get("merge_operation_id") == identity:
                continue
            if old and old["revision"] != pub["location_revision"]:
                raise IntegrityError("vault_merge_publication_revision_changed")
            mapping = {logical: moves[path] for logical, path in pub["mapping"].items()}
            body = set(pub["body_files"])
            hashes = {}
            keys = {}
            copied = {
                f["target_path"]: value["progress"][str(i)] for i, f in enumerate(plan["files"])
            }
            baselines = {}
            for logical, path in mapping.items():
                hashes[path] = copied[path]["sha256"]
                keys[path] = copied[path]["key"]
                if logical in body or logical == "publication-notes.json":
                    raw, key = publication_file_io.read(root, path)
                    if digest(raw) != hashes[path] or key != keys[path]:
                        raise IntegrityError("vault_merge_body_changed_before_binding")
                    baselines[path] = base64.b64encode(raw).decode()
            published_hashes = dict(hashes)
            for logical, path in mapping.items():
                prior = pub["original_hashes"].get(logical)
                source_file = next(
                    f for f in source["files"] if f["path"] == pub["mapping"][logical]
                )
                if prior and source_file["sha256"] != prior:
                    published_hashes[path] = prior  # A merge never certifies a user revision.
            entry = mapping["note.md"]
            package = entry.rsplit("/", 1)[0] if pub["package"] else None
            frozen = {
                **(old["plan"] if old else {}),
                "layout_version": 3,
                "output_storage": "markdown_baselines_and_file_manifest",
                "merge_operation_id": identity,
                "root_key": root_key,
                "policy_revision": plan["target_policy"]["revision"],
                "policy_binding": policy,
                "directories": {},
                "claims": {p: k[:2] for p, k in keys.items()},
                "file_keys": keys,
                "mapping": mapping,
                "outputs": baselines,
                "body_files": sorted(body),
                "published_hashes": published_hashes,
                "write_hashes": hashes,
                "identity_overrides": {
                    logical: next(
                        n["identity"]
                        for n in source["notes"]
                        if n["path"] == pub["mapping"][logical]
                    )
                    for logical in body
                    if any(n["path"] == pub["mapping"][logical] for n in source["notes"])
                },
            }
            if pub.get("intake_source"):
                frozen["intake_source"] = pub["intake_source"]
            if pub["kind"] == "document":
                frozen.setdefault("body_projections", {})
                for logical in body:
                    frozen["body_projections"].setdefault(
                        logical,
                        {
                            "original_sha256": pub["original_hashes"].get(logical),
                            "published_sha256": published_hashes[mapping[logical]],
                        },
                    )
            if "source_spans" in frozen:
                frozen["source_spans"] = {
                    **frozen["source_spans"],
                    "status": "merged_links_offsets_unavailable",
                    "published_units": None,
                    "published_note_sha256": published_hashes[entry],
                }
            if "body_projections" in frozen:
                for logical, projection in frozen["body_projections"].items():
                    projection["published_sha256"] = published_hashes[mapping[logical]]
            db.execute(
                "INSERT INTO publication_locations VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(kind,publication_id) DO UPDATE SET vault_id=excluded.vault_id,"
                "entry_note_path=excluded.entry_note_path,package_root=excluded.package_root,"
                "revision=excluded.revision,state=excluded.state,value=excluded.value",
                (
                    pub["kind"],
                    pub["id"],
                    target,
                    entry,
                    package,
                    pub["location_revision"] + 1,
                    "published",
                    canonical(frozen),
                ),
            )
        existing = {r.identity: r for r in note_identity_store.records(config, target)}
        for file in [f for f in plan["files"] if f["source_vault_id"] == vid and f["note"]]:
            note = file["note"]
            raw, key = publication_file_io.read(root, file["target_path"])

            revisions = value.setdefault("identity_revisions", {})
            if note["identity"] not in revisions:
                with note_identity_store.connection(config) as side:
                    revisions[note["identity"]] = (
                        side.execute(
                            "SELECT COALESCE(max(revision),0) FROM events WHERE vault_id=? AND"
                            " identity=?",
                            (target, note["identity"]),
                        ).fetchone()[0]
                        if side
                        else 0
                    ) + 1
                save(db, identity, value, "rebinding")
            registration = note_identity_store.Registration(
                note["identity"],
                note["publication_id"],
                file["target_path"],
                digest(raw),
                tuple(key),
                (root_key[0], root_key[1]),
                revisions[note["identity"]],
            )
            if registration.identity in existing:
                if existing[registration.identity] != registration:
                    raise IntegrityError("vault_merge_identity_collision")
            else:
                note_identity_store.save(config, target, client, registration)
        for m in source["mocs"]:
            old_record = moc_store.load(config, target, m["id"])

            revisions = value.setdefault("moc_revisions", {})
            if m["id"] not in revisions:
                with moc_store.connection(config) as side:
                    revisions[m["id"]] = (
                        side.execute(
                            "SELECT COALESCE(max(revision),0) FROM events WHERE vault_id=? AND"
                            " moc_id=?",
                            (target, m["id"]),
                        ).fetchone()[0]
                        if side
                        else 0
                    ) + 1
                save(db, identity, value, "rebinding")
            wanted = moc_store.Record(
                m["record"]["title"],
                root_key[0],
                root_key[1],
                revisions[m["id"]],
                m["record"]["members"],
            )
            if old_record is None:
                moc_store.save(config, target, m["id"], client, wanted, "merge_from:" + vid, 0)
            elif old_record != wanted:
                raise IntegrityError("vault_merge_moc_conflict")
            if m["file_path"]:
                moc_baseline(config, db, identity, target, m, moves[m["file_path"]], root)


def moc_baseline(
    config: Config, db: Any, operation: str, target: str, moc: dict[str, Any], path: str, root: Path
) -> None:
    """A verified copied managed block becomes a new baseline with an explicit merge audit."""
    from uuid import NAMESPACE_URL, uuid5

    from . import moc_file_journal, moc_markdown

    plans = moc_file_journal.plans(config, target, moc["id"])
    operation_id = str(uuid5(NAMESPACE_URL, operation + ":moc:" + moc["id"]))
    current = moc_file_journal.latest(plans)
    if current:
        if current.value["operation"] != operation_id:
            raise IntegrityError("vault_merge_moc_baseline_changed")
        return
    existing_plan = next((p for p in plans if p.value["operation"] == operation_id), None)
    raw, key = publication_file_io.read(root, path)
    _, block, _ = moc_markdown.split(raw, moc["id"])
    if not block:
        return  # The whole legacy MOC is human-owned; no block is invented.
    parent = (root / path).parent.stat()
    record = moc_store.load(config, target, moc["id"])
    if record is None:
        raise IntegrityError("vault_merge_moc_missing")
    value = {
        "schema": 1,
        "operation": str(uuid5(NAMESPACE_URL, operation + ":moc:" + moc["id"])),
        "vault_id": target,
        "moc_id": moc["id"],
        "revision": record.revision,
        "path": path,
        "before_sha256": digest(raw),
        "after_sha256": digest(raw),
        "file_key": key[:2],
        "vault_key": vault_directories.key(root),
        "parent_key": [parent.st_dev, parent.st_ino],
        "inventory_sha256": digest(canonical({"merge": operation}).encode()),
        "managed_sha256": digest(block),
        "base_id": None,
        "manual_links_preserved": 0,
    }
    created = existing_plan or moc_file_journal.create(config, value, raw)
    if created.value != value:
        raise IntegrityError("vault_merge_moc_baseline_changed")
    if not (created.path / "claim").exists():
        moc_file_journal.write(created.path / "claim", b"claimed\n")
    if not (created.path / "outcome").exists():
        moc_file_journal.write(created.path / "outcome", b"applied\n")
