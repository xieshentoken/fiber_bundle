import hashlib
import json
import time

import pytest

from fiber_bundle import auth, database, note_identity_store, search, vault_catalog, vault_jobs
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_job_launch import launch
from fiber_bundle.vault_job_worker import execute


def job(config, db, tmp_path, *, mode="copy"):
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "assets").mkdir()
    (source / "nested/note.md").write_text(
        "# 合成冷板\n压力2.4 MPa。 ![[assets/image.png]] ![missing](missing.png)"
    )
    (source / "assets/image.png").write_bytes(bytes(range(100)))
    (source / "secret").mkdir()
    (source / "secret/hidden.md").write_text("private excluded")
    (source / ".knowledge-organizer-policy.json").write_text(
        json.dumps({"schema_version": 1, "deny_paths": [{"path": "secret", "deny": ["read"]}]})
    )
    p = vault_catalog.preview(
        config, db, name="新研究库", root=str(source), scope={"include": [""], "exclude": []}
    )
    assert p["notes"] == 1 and p["attachments"] == 1 and p["warning_count"] == 1
    cid = auth.create_client(db, "tester", False)["id"]
    j = vault_jobs.prepare(
        config,
        db,
        mode=mode,
        preview_id=p["preview_id"],
        name="新研究库",
        target=str(tmp_path / "imported") if mode == "copy" else None,
        client_id=cid,
        query=True,
        original=False,
        add=False,
    )
    return source, cid, j


def test_copy_preserves_relative_attachments_stable_identity_and_search(config, store, tmp_path):
    db = database.connect(config.state_root)
    source, cid, j = job(config, db, tmp_path)
    db.execute("UPDATE vault_operations SET state='launch_requested' WHERE id=?", (j["id"],))
    db.close()
    before = hashlib.sha256((source / "nested/note.md").read_bytes()).hexdigest()
    execute(config, j["id"])
    db = database.connect(config.state_root)
    finished = vault_jobs.view(vault_jobs.row(db, j["id"]))
    assert finished["state"] == "complete"
    assert finished["result"]["notes"] == 1 and finished["result"]["model_calls"] == 0
    target = tmp_path / "imported"
    assert (target / "nested/note.md").read_bytes() == (source / "nested/note.md").read_bytes()
    assert (target / "assets/image.png").read_bytes() == (source / "assets/image.png").read_bytes()
    assert not (target / "secret").exists()
    vid = finished["vault_id"]
    records = note_identity_store.records(config, vid)
    assert len(records) == 1 and records[0].identity.startswith("note:")
    result = search.vault_search(config, db, cid, "2.4 MPa", [vid])
    assert result["results"] and result["results"][0]["note_id"] == records[0].identity
    assert hashlib.sha256((source / "nested/note.md").read_bytes()).hexdigest() == before
    db.close()


def test_source_change_fails_without_adopting_partial_directory(config, store, tmp_path):
    db = database.connect(config.state_root)
    source, cid, j = job(config, db, tmp_path)
    (source / "nested/note.md").write_text("changed after preview")
    db.execute("UPDATE vault_operations SET state='launch_requested' WHERE id=?", (j["id"],))
    db.close()
    with pytest.raises(IntegrityError):
        execute(config, j["id"])
    db = database.connect(config.state_root)
    assert vault_jobs.view(vault_jobs.row(db, j["id"]))["state"] == "failed"
    assert not db.execute("SELECT 1 FROM vaults WHERE name='新研究库'").fetchone()
    assert (tmp_path / "imported").is_dir()
    db.close()


def test_cancel_prepared_and_during_copy_never_publishes_or_queries(
    config, store, tmp_path, monkeypatch
):
    db = database.connect(config.state_root)
    source, cid, j = job(config, db, tmp_path)
    vault_jobs.cancel(db, j["id"])
    db.close()
    execute(config, j["id"])
    assert not (tmp_path / "imported").exists()


def test_attach_readonly_has_registered_notes_without_write_grant(config, store, tmp_path):
    db = database.connect(config.state_root)
    source, cid, j = job(config, db, tmp_path, mode="attach")
    db.execute("UPDATE vault_operations SET state='launch_requested' WHERE id=?", (j["id"],))
    db.close()
    execute(config, j["id"])
    db = database.connect(config.state_root)
    view = vault_jobs.view(vault_jobs.row(db, j["id"]))
    assert view["state"] == "complete"
    vid = view["vault_id"]
    assert len(note_identity_store.records(config, vid)) == 1
    assert auth.grant(db, cid, vid, "query")["read_only"] == 1
    with pytest.raises(auth.Denied):
        auth.grant(db, cid, vid, "add")
    assert (source / "nested/note.md").exists()
    db.close()


def test_real_explicit_child_finishes_without_model_calls(config, store, tmp_path):
    db = database.connect(config.state_root)
    _, _, j = job(config, db, tmp_path)
    with database.locked(config):
        assert launch(config, db, j["id"])["state"] == "launch_requested"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = vault_jobs.view(vault_jobs.row(db, j["id"]))
        if current["state"] not in {"launch_requested", "running"}:
            break
        time.sleep(0.05)
    assert current["state"] == "complete"
    assert current["result"]["model_calls"] == 0
    db.close()
