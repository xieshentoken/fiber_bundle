"""Reviewed MOC intents using the existing ownership, file plan and reconciliation contracts."""

import json
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any

from .. import (
    auth,
    database,
    moc_associations,
    moc_file_journal,
    moc_files,
    moc_markdown,
    moc_store,
    note_changes,
    note_search_inventory,
)
from ..config import Config, IntegrityError, InvalidInput
from ..inputs import canonical
from . import catalog, intake, jobs, store
from .contracts import fingerprint

MAX_INTENTS = 100


def _row(config: Config, cid: str, vid: str, wid: str) -> dict[str, Any]:
    with store.connection(config) as db:
        if db is None:
            raise auth.Denied(404)
        row = db.execute(
            "SELECT * FROM writebacks WHERE id=? AND client_id=? AND vault_id=?", (wid, cid, vid)
        ).fetchone()
        if row is None:
            raise auth.Denied(404)
        value = dict(row)
        value["request"] = json.loads(value.pop("request_json"))
        value["result"] = json.loads(value.pop("result_json") or "{}")
        return value


def _update(config: Config, wid: str, state: str, result: dict[str, Any]) -> None:
    with store.connection(config, write=True) as db:
        assert db is not None
        with database.transaction(db):
            db.execute(
                "UPDATE writebacks SET state=?,result_json=? WHERE id=?",
                (state, canonical(result), wid),
            )


def authorize(config: Config, cid: str, vid: str, wid: str) -> dict[str, Any]:
    jobs.authorize(config, cid, vid)
    row = _row(config, cid, vid, wid)
    request = row["request"]
    db = database.connect(config.state_root)
    try:
        auth.grant(db, cid, vid, "add")
        if intake.epoch(db, cid, vid) != request["permission_epoch"]:
            raise IntegrityError("graph_writeback_authorization_changed")
    finally:
        db.close()
    if request["action"] == "forward":
        from .writeback_sources import current

        with database.connect(config.state_root) as main:
            snapshot = current(
                config, main, auth.grant(main, cid, vid, "query"), wid, request["moc_id"]
            )
        value = catalog.generation(config, cid, vid, inventory_snapshot=snapshot)
        if value is None or value.profile.digest != request["configuration"]:
            raise IntegrityError("graph_writeback_stale")
        suggestion = next(
            (
                o
                for o in catalog.objects(value)
                if o["id"] == row["object_id"] and o["type"] == "moc"
            ),
            None,
        )
        if suggestion is None or not suggestion["support"] <= value.visible:
            raise IntegrityError("graph_writeback_stale")
        with store.connection(config) as side:
            assert side is not None
            if (
                catalog.decision(side, cid, vid, value.profile.digest, row["object_id"])["state"]
                != "accepted"
            ):
                raise IntegrityError("graph_writeback_review_required")
    return row


def _record(raw: dict[str, Any] | None) -> moc_store.Record | None:
    if raw is None:
        return None
    value = moc_store.Record(**raw)
    moc_store.validate(value)
    return value


def prepare(
    config: Config,
    cid: str,
    vid: str,
    oid: str,
    *,
    moc_id: str,
    revision: int,
    relative: str,
    expected_sha256: str,
    undo: str | None = None,
    _locked: bool = False,
) -> dict[str, Any]:
    note_changes.identifier(moc_id)
    if type(revision) is not int or revision < 0:
        raise InvalidInput("graph_moc_revision_required")
    with nullcontext() if _locked else database.locked(config):
        jobs.authorize(config, cid, vid)
        db = database.connect(config.state_root)
        try:
            vault = auth.grant(db, cid, vid, "add")
            auth.grant(db, cid, vid, "query")
            inventory = note_search_inventory.read(config, db, vault, selected=set())
            root = inventory.root.stat()
            current = moc_store.load(config, vid, moc_id)
            if (current.revision if current else 0) != revision:
                raise IntegrityError("MOC revision conflict")
            if current and (current.vault_dev, current.vault_ino) != (root.st_dev, root.st_ino):
                raise IntegrityError("MOC vault identity changed")
            members = {k: v.copy() for k, v in current.members.items()} if current else {}
            with store.connection(config) as side:
                history = (
                    [
                        dict(r)
                        for r in side.execute("SELECT * FROM writebacks WHERE vault_id=?", (vid,))
                    ]
                    if side
                    else []
                )
            if undo:
                previous = _row(config, cid, vid, undo)
                if (
                    previous["state"] != "completed"
                    or previous["request"]["moc_id"] != moc_id
                    or previous["request"]["action"] != "forward"
                ):
                    raise IntegrityError("graph_writeback_not_reversible")
                # Any metadata mutation outside this ledger defeats attribution, so fail closed.
                tracked = [
                    json.loads(r["request_json"])
                    for r in history
                    if r["state"] in {"completed", "cancelled"}
                    and json.loads(r["request_json"])["moc_id"] == moc_id
                ]
                if not current or not any(t["after_record"] == asdict(current) for t in tracked):
                    raise IntegrityError("graph_moc_ownership_requires_reconciliation")
                owned = {n for t in tracked if t["action"] == "forward" for n in t["added"]}
                shared = {
                    n
                    for r in history
                    if r["id"] != undo and r["state"] in {"completed", "applying", "unknown"}
                    for n in json.loads(r["request_json"])["members"]
                    if json.loads(r["request_json"])["moc_id"] == moc_id
                    and json.loads(r["request_json"])["action"] == "forward"
                }
                for n in set(previous["request"]["members"]) & owned - shared:
                    owners = [o for o in members.get(n, []) if o != "system"]
                    if owners:
                        members[n] = owners
                    else:
                        members.pop(n, None)
                title = current.title
                configuration = previous["request"]["configuration"]
                wanted = previous["request"]["members"]
                added = []
            else:
                value = catalog.generation(config, cid, vid)
                if value is None:
                    raise IntegrityError("graph_generation_unavailable")
                suggestion = next(
                    (o for o in catalog.objects(value) if o["id"] == oid and o["type"] == "moc"),
                    None,
                )
                if suggestion is None or not suggestion["support"] <= value.visible:
                    raise IntegrityError("graph_suggestion_stale")
                with store.connection(config) as side:
                    assert side is not None
                    if (
                        catalog.decision(side, cid, vid, value.profile.digest, oid)["state"]
                        != "accepted"
                    ):
                        raise IntegrityError("graph_writeback_review_required")
                # Reconfirmation is idempotent after the resulting revision changes.
                for row in history:
                    old = json.loads(row["request_json"])
                    if (
                        row["client_id"] == cid
                        and row["object_id"] == oid
                        and old["moc_id"] == moc_id
                        and old["action"] == "forward"
                        and row["state"] == "completed"
                    ):
                        return _row(config, cid, vid, row["id"])
                wanted = sorted(suggestion["support"])
                added = [n for n in wanted if "system" not in members.get(n, [])]
                for n in wanted:
                    members[n] = sorted(set(members.get(n, [])) | {"system"})
                title = current.title if current else suggestion["value"]["title"]
                configuration = value.profile.digest
            after = moc_store.Record(title, root.st_dev, root.st_ino, revision + 1, members)
            moc_store.validate(after)
            data, key = moc_files.target(inventory, relative)
            actual = moc_files.digest(data) if key is not None else "absent"
            if actual != expected_sha256:
                raise IntegrityError("MOC file hash conflict")
            prior = moc_file_journal.latest(moc_file_journal.plans(config, vid, moc_id))
            _, block, _ = moc_markdown.split(data, moc_id)
            if prior and moc_files.digest(block) != prior.value["managed_sha256"]:
                raise IntegrityError("MOC managed block human edited")
            if not prior and block:
                raise IntegrityError("unowned MOC managed block")
            body = (
                data
                if key is not None
                else ("---\ntype: moc\n---\n# " + moc_associations.escape(title) + "\n\n").encode()
            )
            candidate, _, _ = moc_markdown.render(body, moc_id, relative, after, inventory)
            request = {
                "action": "undo" if undo else "forward",
                "undo_of": undo,
                "moc_id": moc_id,
                "path": relative,
                "expected_sha256": actual,
                "before_record": asdict(current) if current else None,
                "after_record": asdict(after),
                "members": wanted,
                "added": added,
                "configuration": configuration,
                "permission_epoch": intake.epoch(db, cid, vid),
                "preview_sha256": moc_files.digest(candidate),
            }
            wid = fingerprint(
                {"client_id": cid, "vault_id": vid, "object_id": oid, "request": request}
            )
            with store.connection(config, write=True) as side:
                assert side is not None
                with database.transaction(side):
                    if side.execute("SELECT count(*) FROM writebacks").fetchone()[0] >= MAX_INTENTS:
                        raise InvalidInput("graph_writeback_limit")
                    side.execute(
                        "INSERT OR IGNORE INTO writebacks VALUES(?,?,?,?,?,?,?)",
                        (wid, cid, vid, oid, canonical(request), "prepared", None),
                    )
            return {**_row(config, cid, vid, wid), "markdown": candidate.decode("utf-8-sig")}
        finally:
            db.close()


def apply(
    config: Config, cid: str, vid: str, wid: str, *, confirm: bool = False, _locked: bool = False
) -> dict[str, Any]:
    if confirm is not True:
        raise InvalidInput("graph_moc_confirmation_required")
    with nullcontext() if _locked else database.locked(config):
        row = authorize(config, cid, vid, wid)
        if row["state"] == "completed":
            return row
        if row["state"] != "prepared":
            raise IntegrityError("graph_writeback_requires_reconciliation")
        request = row["request"]
        mid = request["moc_id"]
        before = _record(request["before_record"])
        after = _record(request["after_record"])
        assert after
        if moc_store.load(config, vid, mid) != before:
            raise IntegrityError("MOC revision conflict")
        _update(config, wid, "applying", {})
        db = database.connect(config.state_root)
        try:
            moc_store.save(
                config,
                vid,
                mid,
                cid,
                after,
                "graph-" + request["action"],
                before.revision if before else 0,
            )
            plan = moc_files.plan_file(
                config,
                db,
                cid,
                vid,
                mid,
                after.revision,
                request["path"],
                request["expected_sha256"],
                graph_writeback_id=wid,
            )
            if (
                plan.get("after_sha256") != request["preview_sha256"]
                and plan["status"] != "unchanged"
            ):
                raise IntegrityError("graph_moc_preview_changed")
            _update(
                config, wid, "applying", {"plan_id": plan.get("plan_id"), "metadata_written": True}
            )
            if plan.get("plan_id"):
                moc_files.apply_file(config, db, cid, vid, mid, plan["plan_id"])
            from ..organization.note_moc_backfill import sync_members

            note_written = sync_members(
                config,
                db,
                cid,
                vid,
                set(after.members) | (set(before.members) if before else set()),
            )
            result: dict[str, Any] = {"plan_id": plan.get("plan_id"), "applied": True}
            result["note_write_performed"] = note_written
            if not request["undo_of"]:
                from ..note_placement import prepare_after_moc

                archives = prepare_after_moc(config, db, cid, vid, list(after.members))
                if archives:
                    result["archive_previews"] = archives
            _update(config, wid, "completed", result)
            if request["undo_of"]:
                previous = _row(config, cid, vid, request["undo_of"])
                _update(config, previous["id"], "cancelled", previous["result"])
            return _row(config, cid, vid, wid)
        except Exception:
            latest = _row(config, cid, vid, wid)
            _update(config, wid, "unknown", latest["result"])
            raise
        finally:
            db.close()


def reconcile(
    config: Config, cid: str, vid: str, wid: str, *, _locked: bool = False
) -> dict[str, Any]:
    with nullcontext() if _locked else database.locked(config):
        row = authorize(config, cid, vid, wid)
        if row["state"] not in {"unknown", "applying"}:
            return row
        request = row["request"]
        mid = request["moc_id"]
        current = moc_store.load(config, vid, mid)
        if current == _record(request["before_record"]):
            _update(config, wid, "cancelled", {"metadata_written": False})
            return _row(config, cid, vid, wid)
        if current != _record(request["after_record"]):
            raise IntegrityError("graph_moc_reconcile_conflict")
        plans = [
            p
            for p in moc_file_journal.plans(config, vid, mid)
            if p.value.get("graph_writeback_id") == wid
        ]
        if len(plans) != 1:
            raise IntegrityError("graph_moc_reconcile_requires_manual_review")
        plan = plans[0]
        db = database.connect(config.state_root)
        try:
            if plan.status == "pending":
                result = moc_files.apply_file(
                    config, db, cid, vid, mid, plan.plan_id, reconcile=True
                )
            elif plan.status == "applied":
                result = moc_files.apply_file(config, db, cid, vid, mid, plan.plan_id)
            else:
                raise IntegrityError("graph_moc_not_written_no_replay")
            if result["status"] != "applied":
                raise IntegrityError("graph_moc_not_written_no_replay")
            from ..organization.note_moc_backfill import sync_members

            before = _record(request["before_record"])
            assert current is not None
            sync_members(
                config,
                db,
                cid,
                vid,
                set(current.members) | (set(before.members) if before else set()),
            )
            _update(config, wid, "completed", {"plan_id": plan.plan_id, "applied": True})
            if request["undo_of"]:
                previous = _row(config, cid, vid, request["undo_of"])
                _update(config, previous["id"], "cancelled", previous["result"])
            return _row(config, cid, vid, wid)
        finally:
            db.close()


def cancel(
    config: Config, cid: str, vid: str, wid: str, *, _locked: bool = False
) -> dict[str, Any]:
    with nullcontext() if _locked else database.locked(config):
        jobs.authorize(config, cid, vid)
        row = _row(config, cid, vid, wid)
        if row["state"] == "prepared":
            _update(config, wid, "cancelled", {})
        elif row["state"] != "cancelled":
            raise IntegrityError("graph_writeback_cannot_cancel_after_claim")
        return _row(config, cid, vid, wid)
