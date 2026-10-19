import base64
import json
from unittest.mock import patch
from uuid import uuid4

from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_jobs import ready as ready
from test_note_placement import published

from fiber_bundle import database, publication_file_io, publication_locations
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import (
    jobs,
    note_moc_fields,
    note_moc_locations,
    note_moc_sync,
    store,
)


def managed(config, ready):
    cid, vid, root, _, note = ready
    raw, key = publication_file_io.read(root, "Inbox/传热.md")
    identity = str(uuid4())
    plan = {
        "layout_version": 3,
        "mapping": {"note.md": "Inbox/传热.md"},
        "body_files": ["note.md"],
        "outputs": {"Inbox/传热.md": base64.b64encode(raw).decode()},
        "published_hashes": {"Inbox/传热.md": digest(raw)},
        "file_keys": {"Inbox/传热.md": key},
        "claims": {"Inbox/传热.md": key[:2]},
    }
    with database.connect(config.state_root) as db:
        policy = db.execute(
            "SELECT value FROM vault_save_policies WHERE vault_id=?", (vid,)
        ).fetchone()
        plan["policy_binding"] = json.loads(policy[0])
        db.execute(
            "INSERT INTO publication_locations "
            "VALUES('intake',?,?,'Inbox/传热.md',NULL,1,'published',?)",
            (identity, vid, canonical(plan)),
        )
    return identity, raw, plan


def test_managed_intake_note_remains_visible_and_baseline_preserves_human_edits(config, ready):
    cid, vid, _, _, note = ready
    identity, generated, original = managed(config, ready)
    note.write_bytes(note.read_bytes() + "用户新增正文。\n".encode())
    with database.connect(config.state_root) as db:
        result = jobs.run(config, db, jobs.submit(config, db, cid, vid))
        assert result["state"] == "completed", result
        es, _, _ = jobs.snapshot(config, db, cid, vid)
        assert len(es) == 1 and es[0]["path"] == "Inbox/传热.md"
        location = publication_locations.get(db, "intake", identity)
        assert location["revision"] == 2
        assert location["plan"]["file_keys"]["Inbox/传热.md"] == es[0]["file_key"]
        baseline = base64.b64decode(location["plan"]["outputs"]["Inbox/传热.md"])
        assert baseline.endswith(generated) and "用户新增正文".encode() not in baseline
        assert note.read_bytes().endswith(generated + "用户新增正文。\n".encode())
        assert note_moc_fields.field(baseline.decode())[2] == ["[[Map/热管理 MOC]]"]
        assert not jobs.submit(config, db, cid, vid)["items"]


def test_location_commit_interruption_is_recoverable(config, ready):
    cid, vid, *_ = ready
    managed(config, ready)
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        with patch.object(note_moc_locations, "apply", side_effect=OSError("location interrupted")):
            failed = jobs.run(config, db, job)
        assert failed["state"] == "partial"
        assert store.rows(config, "note_moc", vid)[0]["pending"]
        resumed = jobs.action(config, db, cid, vid, job["id"], "resume")
        assert jobs.run(config, db, resumed)["state"] == "completed"
        assert jobs.snapshot(config, db, cid, vid)[0]


def test_completed_old_projection_repairs_managed_location_once(config, ready):
    cid, vid, *_ = ready
    identity, _, _ = managed(config, ready)
    with database.connect(config.state_root) as db:
        job = jobs.submit(config, db, cid, vid)
        with patch.object(note_moc_locations, "apply", return_value=None):
            assert jobs.run(config, db, job)["state"] == "completed"
        record = store.rows(config, "note_moc", vid)[0]
        record.pop("location_synced")
        store.put(config, "note_moc", note_moc_sync.key(vid, record["entity"]), vid, record)
        note_moc_sync.recover_all(config, db, cid, vid)
        assert jobs.snapshot(config, db, cid, vid)[0]
        revision = publication_locations.get(db, "intake", identity)["revision"]
        note_moc_sync.recover_all(config, db, cid, vid)
        assert publication_locations.get(db, "intake", identity)["revision"] == revision


def test_published_article_classification_keeps_flat_publication_readable(
    config, policy, article_scope
):
    from fiber_bundle.organization import membership

    _, result, note = published(config, policy, article_scope)
    cid, vid = article_scope[:2]
    root = note.parent.parent
    moc = root / "Map/主题 MOC.md"
    moc.parent.mkdir(exist_ok=True)
    moc.write_text("# 主题 MOC\n人工说明\n")
    before = note.read_bytes()
    with database.connect(config.state_root) as db:
        es, ms, _ = jobs.snapshot(config, db, cid, vid)
        entity = next(e for e in es if e["path"] == result["note_path"])
        target = next(m for m in ms if m["path"] == "Map/主题 MOC.md")
        assert membership.apply(config, db, cid, vid, entity, target)
        current = next(e for e in jobs.snapshot(config, db, cid, vid)[0] if e["id"] == entity["id"])
        assert current["publication_id"] == result["publication_id"]
        text = note.read_text()
        start, end, values, _, _ = note_moc_fields.field(text)
        assert (text[:start] + text[end:]).encode() == before
        assert values == ["[[Map/主题 MOC]]"]
        location = publication_locations.get(db, "article", result["publication_id"])
        assert location["plan"]["published_hashes"][result["note_path"]] == digest(
            note.read_bytes()
        )
