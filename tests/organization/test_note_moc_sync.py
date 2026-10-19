from unittest.mock import patch

import pytest
from test_jobs import ready as ready

from fiber_bundle import database, moc_store, note_identity_store
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import (
    catalog,
    jobs,
    membership,
    note_moc_backfill,
    note_moc_fields,
    note_moc_sync,
    packages,
    store,
)


@pytest.mark.parametrize("bom", [False, True])
def test_auto_writes_note_map_and_ledger_without_becoming_manual_or_reprocessing(
    config, ready, bom
):
    cid, vid, _, moc, note = ready
    if bom:
        note.write_bytes(b"\xef\xbb\xbf" + note.read_bytes())
    original = note.read_bytes()
    with database.connect(config.state_root) as db:
        first = jobs.run(config, db, jobs.submit(config, db, cid, vid))
        assert first["state"] == "completed", first
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        entity = entities[0]
        assert note_moc_fields.field(note.read_text())[2] == ["[[Map/热管理 MOC]]"]
        assert note.read_bytes().endswith(original.removeprefix(b"\xef\xbb\xbf"))
        assert note.read_bytes().startswith(b"\xef\xbb\xbf") == bom
        assert "传热.md" in moc.read_text()
        assert moc_store.load(config, vid, mocs[0]["id"]).members[entity["id"]] == ["system"]
        assert catalog.manual_memberships(entity, mocs) == []
        assert not any(k.startswith("_") for k in catalog.public_entity(entity))
        record = next(
            r for r in note_identity_store.records(config, vid) if r.identity == entity["id"]
        )
        assert list(record.file_key) == entity["file_key"]
        assert jobs.submit(config, db, cid, vid)["items"] == []
        before = note.read_bytes(), moc.read_bytes()
        assert not membership.apply(config, db, cid, vid, entity, mocs[0])
        assert (note.read_bytes(), moc.read_bytes()) == before


def test_preserve_existing_manual_properties_and_only_remove_fiber_values(config, ready):
    cid, vid, root, _, note = ready
    (root / "Map/人工 MOC.md").write_text("# 人工 MOC\n")
    note.write_text(
        "---\ntitle: '测试' # untouched\nmoc: '[[人工 MOC]]'\n"
        "custom:\n  nested: 42\n---\n#热管理\n正文\n"
    )
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        thermal = next(m for m in mocs if m["path"] == "Map/热管理 MOC.md")
        membership.apply(config, db, cid, vid, entities[0], thermal)
        text = note.read_text()
        assert "title: '测试' # untouched" in text and "custom:\n  nested: 42" in text
        assert note_moc_fields.field(text)[2] == ["[[人工 MOC]]", "[[Map/热管理 MOC]]"]
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        assert catalog.manual_memberships(entities[0], mocs) == [
            next(m["id"] for m in mocs if m["path"] == "Map/人工 MOC.md")
        ]
        membership.apply(
            config, db, cid, vid, entities[0], thermal, owner="manual", action="unlink"
        )
        assert note_moc_fields.field(note.read_text())[2] == ["[[人工 MOC]]"]


def test_backfill_existing_classification_is_idempotent_and_leaves_expert_payload(
    config, ready, package
):
    cid, vid, _, moc, note = ready
    payload = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Book/" + package.name)
        with patch.object(note_moc_sync, "sync", return_value=False):
            initial = jobs.run(config, db, jobs.submit(config, db, cid, vid))
        assert initial["state"] == "completed"
        before = note.read_bytes(), moc.read_bytes()
        packet = note_moc_backfill.plan(config, db, cid, vid)
        assert len(packet["items"]) == 1 and packet["experts_untouched"] == 1
        assert (note.read_bytes(), moc.read_bytes()) == before
        assert note_moc_backfill.apply(config, db, packet)["written"] == 1
        assert moc.read_bytes() == before[1]
        assert note_moc_backfill.apply(config, db, packet)["written"] == 0
        assert not note_moc_backfill.plan(config, db, cid, vid)["items"]
        assert not jobs.submit(config, db, cid, vid)["items"]
    assert {
        p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()
    } == payload


def test_backfill_conflict_preflight_does_not_overwrite_note(config, ready):
    cid, vid, _, _, note = ready
    with database.connect(config.state_root) as db:
        with patch.object(note_moc_sync, "sync", return_value=False):
            jobs.run(config, db, jobs.submit(config, db, cid, vid))
        packet = note_moc_backfill.plan(config, db, cid, vid)
        note.write_text(note.read_text() + "用户新内容\n")
        before = note.read_bytes()
        with pytest.raises(IntegrityError, match="backfill_note_changed"):
            note_moc_backfill.apply(config, db, packet)
        assert note.read_bytes() == before


def test_interrupted_exchange_recovers_identity_then_finishes_without_duplicates(config, ready):
    cid, vid, _, moc, note = ready
    original = note_identity_store.save
    calls = 0

    def fail_after_swap(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption before identity commit")
        return original(*args, **kwargs)

    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        with patch.object(note_identity_store, "save", side_effect=fail_after_swap):
            failed = jobs.run(config, db, job)
        assert failed["state"] == "partial"
        assert store.rows(config, "note_moc", vid)[0]["pending"]
        pending = jobs.action(config, db, cid, vid, job["id"], "resume")
        result = jobs.run(config, db, pending)
        assert result["state"] == "completed", result
        assert "pending" not in store.rows(config, "note_moc", vid)[0]
        assert note_moc_fields.field(note.read_text())[2] == ["[[Map/热管理 MOC]]"]
        assert moc.read_text().count("传热.md") == 1
        assert not jobs.submit(config, db, cid, vid)["items"]


def test_unsupported_frontmatter_does_not_create_classification(config, ready):
    cid, vid, _, moc, note = ready
    note.write_text("---\nmoc: &shared [a]\n---\n#热管理\n内容\n")
    before = note.read_bytes(), moc.read_bytes()
    with database.connect(config.state_root) as db:
        es, ms, _ = jobs.snapshot(config, db, cid, vid)
        with pytest.raises(IntegrityError, match="unsupported_yaml"):
            membership.apply(config, db, cid, vid, es[0], ms[0])
        assert moc_store.load(config, vid, ms[0]["id"]) is None
    assert (note.read_bytes(), moc.read_bytes()) == before


def test_interruption_before_exchange_keeps_original_and_resumes(config, ready):
    cid, vid, _, moc, note = ready
    before = note.read_bytes()
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        with patch.object(note_moc_sync, "recover", side_effect=OSError("before atomic exchange")):
            failed = jobs.run(config, db, job)
        assert failed["state"] == "partial" and note.read_bytes() == before
        pending = jobs.action(config, db, cid, vid, job["id"], "resume")
        assert jobs.run(config, db, pending)["state"] == "completed"
        assert note_moc_fields.field(note.read_text())[2] == ["[[Map/热管理 MOC]]"]
        assert moc.read_text().count("传热.md") == 1


def test_concurrent_edit_at_exchange_is_restored_at_original_path(config, ready):
    cid, vid, _, _, note = ready
    with database.connect(config.state_root) as db:
        with patch.object(note_moc_sync, "sync", return_value=False):
            jobs.run(config, db, jobs.submit(config, db, cid, vid))
        packet = note_moc_backfill.plan(config, db, cid, vid)
        original = note_moc_sync.file_exchange.rename
        edited = False
        user_bytes = note.read_bytes() + "用户并发修改，不能覆盖。\n".encode()

        def concurrent_edit(src_fd, src_name, dst_fd, dst_name, *, exchange):
            nonlocal edited
            if not edited:
                edited = True
                note.write_bytes(user_bytes)
            return original(src_fd, src_name, dst_fd, dst_name, exchange=exchange)

        with patch.object(note_moc_sync.file_exchange, "rename", concurrent_edit):
            with pytest.raises(IntegrityError, match="exchange_conflict_preserved"):
                note_moc_backfill.apply(config, db, packet)
        assert edited and note.read_bytes() == user_bytes
        registration = next(
            r
            for r in note_identity_store.records(config, vid)
            if r.relative_path == "Inbox/传热.md"
        )
        assert registration.file_key[:2] == (note.stat().st_dev, note.stat().st_ino)


def test_unregistered_expert_markdown_cannot_be_written_as_ordinary_note(config, ready, package):
    cid, vid, _, moc, _ = ready
    before = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    map_before = moc.read_bytes()
    with database.connect(config.state_root) as db:
        es, ms, _ = jobs.snapshot(config, db, cid, vid)
        assert not any(e["path"].startswith("Book/fake-thermal/") for e in es)
        # A stale pre-discovery client still cannot bypass the low-level package guard.
        skill = {"id": "stale-skill", "kind": "note", "path": "Book/fake-thermal/SKILL.md"}
        with pytest.raises(IntegrityError, match="expert_package_untouched"):
            membership.apply(config, db, cid, vid, skill, ms[0])
    assert moc.read_bytes() == map_before
    assert {
        p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()
    } == before
