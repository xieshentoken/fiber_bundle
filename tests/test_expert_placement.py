"""Explicit whole-Draft archival preserves every member and provenance ledger."""

import json
import time
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate
from test_source_imports import request, upload

from fiber_bundle import (
    auth,
    database,
    moc_registration,
    moc_store,
    note_inventory,
    note_placement,
    note_placement_execution,
    publication_layout,
    publication_locations,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.objects import digest
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


def publish_fixture(config, cid, vid, files=None):
    """Seed an isolated already-compiled Draft; this does not simulate compiler acceptance."""
    files = files or {
        "note.md": b"# Expert Draft\n![](assets/a.png)\n",
        "assets/a.png": b"image",
        "references/evidence.json": b'{"knowledge_verified":false,"review_state":"deferred"}',
        ".fiber-expert-canonical/private.json": b"{}",
    }
    with database.connect(config.state_root) as db:
        imported = upload(
            config,
            db,
            request(cid, vid, b"synthetic compiled package", "source.bin"),
            b"synthetic compiled package",
        )
        identity = imported["id"]
        db.execute(
            "INSERT INTO source_expert_tasks(id,request_sha256,plan_sha256,"
            "state,result_json,updated_at) "
            "VALUES(?,?,?,'completed',?,?)",
            (
                identity,
                "a" * 64,
                "b" * 64,
                '{"knowledge_verified":false,"review_state":"deferred"}',
                time.time(),
            ),
        )
        location = publication_layout.prepare(
            config, db, "expert", identity, vid, files, expert_inbox=True
        )
        publication_layout.publish(config, db, "expert", identity, cid, files)
        db.execute(
            "INSERT INTO source_expert_publications VALUES(?,?,?,?,?,'published',?)",
            (
                identity,
                "c" * 64,
                '{"knowledge_verified":false}',
                location["package_root"],
                json.dumps({"files": {n: {"sha256": digest(b)} for n, b in files.items()}}),
                time.time(),
            ),
        )
        return identity, location, files


@pytest.fixture
def expert(config, article_scope):
    cid, vid = article_scope[:2]
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    activate(config, vid, **settings)
    identity, location, files = publish_fixture(config, cid, vid)
    root = config.project_root / "test vault/temp_vault1"
    target = root / "Map/Existing.md"
    target.write_text("# Existing manual MOC\nHuman content remains.\n")
    with database.connect(config.state_root) as db:
        p = moc_registration.preview(
            config, db, cid, vid, "Map/Existing.md", digest(target.read_bytes())
        )
        moc_registration.apply(config, db, cid, vid, p["operation_id"], "apply")
    return cid, vid, identity, location, files, p["moc_id"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_whole_draft_archive_keeps_all_bytes_and_identities(config, expert, interrupted):
    cid, vid, identity, location, files, mid = expert
    root = config.project_root / "test vault/temp_vault1"
    with database.connect(config.state_root) as db:
        inv = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        note = next(n for n in inv.notes if n.relative_path == location["entry_note_path"])
        ledger = dict(
            db.execute(
                "select * from source_expert_publications where task_id=?", (identity,)
            ).fetchone()
        )
        packet = note_placement.preview(
            config,
            db,
            cid,
            vid,
            note.identity,
            note.sha256,
            action="expert_link_archive",
            moc_id=mid,
            moc_revision=1,
        )
        assert packet["target_path"].startswith("Book/") and packet["package_files"] == len(files)
        assert all(
            not p.startswith(location["package_root"] + "/") for p in packet["rewritten_notes"]
        )
        if interrupted:
            original = note_placement_execution.move

            def fail_after(*args):
                original(*args)
                raise OSError("simulated after package move")

            with patch.object(note_placement_execution, "move", fail_after), pytest.raises(OSError):
                note_placement_execution.apply(config, db, cid, vid, packet["operation_id"])
        result = note_placement_execution.apply(
            config, db, cid, vid, packet["operation_id"], reconcile=interrupted
        )
        assert result["state"] == "completed" and not (root / location["package_root"]).exists()
        assert {
            p.relative_to(root / result["target_path"]).as_posix(): p.read_bytes()
            for p in (root / result["target_path"]).rglob("*")
            if p.is_file()
        } == files
        assert (
            dict(
                db.execute(
                    "select * from source_expert_publications where task_id=?", (identity,)
                ).fetchone()
            )
            == ledger
        )
        assert moc_store.load(config, vid, mid).members[note.identity] == ["manual"]
        assert "Book/Expert%20Draft-Draft/note.md" in (root / "Map/Existing.md").read_text()
        new = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        assert next(n for n in new.notes if n.identity == note.identity).relative_path.startswith(
            "Book/"
        )
        assert publication_locations.get(db, "expert", identity)["state"] == "published"
        assert (
            note_placement_execution.apply(config, db, cid, vid, packet["operation_id"])["state"]
            == "completed"
        )


@pytest.mark.parametrize("fault", ["ordinary_action", "collision", "changed_member", "revoked"])
def test_draft_archive_rejects_unsafe_inputs_without_moving(config, expert, fault):
    cid, vid, identity, location, files, mid = expert
    root = config.project_root / "test vault/temp_vault1"
    with database.connect(config.state_root) as db:
        inv = note_inventory.read(config, db, auth.grant(db, cid, vid, "query"))
        note = next(n for n in inv.notes if n.relative_path == location["entry_note_path"])
        if fault == "collision":
            (root / "Book" / __import__("pathlib").Path(location["package_root"]).name).mkdir()
        if fault in {"ordinary_action", "collision"}:
            with pytest.raises((IntegrityError, InvalidInput)):
                note_placement.preview(
                    config,
                    db,
                    cid,
                    vid,
                    note.identity,
                    note.sha256,
                    action="archive" if fault == "ordinary_action" else "expert_archive",
                )
        else:
            p = note_placement.preview(
                config, db, cid, vid, note.identity, note.sha256, action="expert_archive"
            )
            if fault == "changed_member":
                (root / location["package_root"] / "assets/a.png").write_bytes(b"edited")
            else:
                auth.set_grant(db, cid, vid, query=True, original=True, add=False)
            with pytest.raises((IntegrityError, auth.Denied)):
                note_placement_execution.apply(config, db, cid, vid, p["operation_id"])
        assert (root / location["package_root"]).exists()
