import hashlib
import json
import os
import sqlite3
from dataclasses import replace

import pytest
from schema_support import remove_graph_epoch_schema

from fiber_bundle import auth, backup_preflight, database, note_identities, restore_rebind
from fiber_bundle.config import IntegrityError, InvalidInput, load_config
from fiber_bundle.inputs import canonical
from fiber_bundle.knowledge_graph import catalog, intake, jobs, policy, store, vectors
from fiber_bundle.knowledge_graph.contracts import Engine, Model

from .support import related_result


@pytest.fixture
def small(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "state_root": ".state",
                "vaults": {
                    "personal": "test vault/temp_vault1",
                    "supplier": "test vault/temp_vault2",
                },
            }
        )
    )
    cfg = load_config(path, tmp_path)
    database.initialize(cfg)
    db = database.connect(cfg.state_root)
    client = auth.create_client(db, "test", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=True, original=False, add=True)
    second = auth.create_client(db, "other", False)
    auth.set_grant(db, second["id"], other, query=True, original=False, add=True)
    db.close()
    token = "Bearer " + client["token"]
    selected = []
    for i, body in enumerate(
        [
            "# 青松\n青松供应商的额定压力为2.4 MPa，材料型号AB-12。\n",
            "# 海星\n海星供应商压力2.4 MPa\n",
            "# 青松作家\n这是一位小说作家。\n",
        ]
    ):
        target = tmp_path / "test vault/temp_vault1" / f"n{i}.md"
        target.write_text(body)
        if not hasattr(target.stat(), "st_birthtime"):
            pytest.skip("physical identity requires birthtime")
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        tracked = note_identities.run(cfg, token, vid, "track", target.name, sha)
        selected.append(intake.Selection(tracked["note_identity"], sha))
    profile = policy.Profile(1, True, "sha256:" + "1" * 64, Engine())
    with database.locked(cfg):
        policy.save(cfg, profile, None)
    return cfg, client["id"], vid, tuple(selected), token, second["id"], other


def fake_build(cfg, prepared, worker_policy, cache, *, check, cancelled):
    check()
    raw = canonical(related_result(prepared.request)).encode()
    return vectors.Build(catalog.decode_result(raw, prepared.request), raw, {}, 0)


def finish(small, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    monkeypatch.setattr(jobs.worker, "run_build", fake_build)
    job = jobs.submit(cfg, cid, vid, selected)
    assert jobs.execute(cfg, cid, vid, job["id"])["state"] == "completed"
    return job


def test_epoch_revoke_restore_and_noop(small):
    cfg, cid, vid, selected, token, *_ = small
    prepared = intake.prepare(cfg, token, vid, selected, enabled=True)
    db = database.connect(cfg.state_root)
    auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    assert intake.epoch(db, cid, vid) == prepared.permission_epoch
    auth.set_grant(db, cid, vid, query=False, original=False, add=True)
    auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    assert intake.epoch(db, cid, vid) > prepared.permission_epoch
    db.close()
    with pytest.raises(IntegrityError):
        intake.revalidate(cfg, token, prepared, enabled=True)


def test_client_and_vault_disable_restore_epoch(small):
    cfg, cid, vid, selected, token, *_ = small
    prepared = intake.prepare(cfg, token, vid, selected, enabled=True)
    db = database.connect(cfg.state_root)
    db.execute("UPDATE clients SET active=0 WHERE id=?", (cid,))
    db.execute("UPDATE clients SET active=1 WHERE id=?", (cid,))
    db.execute("UPDATE vaults SET status='pending' WHERE id=?", (vid,))
    db.execute("UPDATE vaults SET status='active' WHERE id=?", (vid,))
    db.close()
    with pytest.raises(IntegrityError):
        intake.revalidate(cfg, token, prepared, enabled=True)


def test_epoch_delete_recreate(small):
    cfg, cid, vid, selected, token, *_ = small
    prepared = intake.prepare(cfg, token, vid, selected, enabled=True)
    db = database.connect(cfg.state_root)
    db.execute("DELETE FROM client_vault_grants WHERE client_id=? AND vault_id=?", (cid, vid))
    auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    assert intake.epoch(db, cid, vid) == prepared.permission_epoch + 2
    db.close()


def test_schema20_migration_preserves_rows_and_backup(small):
    cfg, *_ = small
    path = cfg.state_root / "metadata.sqlite3"
    db = sqlite3.connect(path)
    remove_graph_epoch_schema(db)
    db.execute("PRAGMA user_version=20")
    db.commit()
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {n: db.execute("SELECT * FROM " + n + " ORDER BY rowid").fetchall() for n in names}
    db.close()
    snapshot = database.migrate(cfg)
    assert snapshot and snapshot.stat().st_mode & 0o777 == 0o600
    db = database.connect(cfg.state_root)
    assert {
        n: [
            tuple(r)[:-5] if n == "vaults" else tuple(r)
            for r in db.execute("SELECT * FROM " + n + " ORDER BY rowid")
        ]
        for n in names
    } == before
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert db.execute("SELECT count(*) FROM graph_permission_epochs").fetchone()[0] == 2
    db.close()
    old = sqlite3.connect(snapshot)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 20
    old.close()


def test_disabled_reads_create_no_graph_state(small, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    cfg.state_root.joinpath("knowledge-graph-policy.json").unlink()
    monkeypatch.setattr(jobs.worker, "run_build", lambda *a, **k: pytest.fail("worker"))
    assert catalog.browse(cfg, cid, vid)["items"] == []
    assert jobs.status(cfg, cid, vid) == []
    with pytest.raises(InvalidInput, match="disabled"):
        jobs.submit(cfg, cid, vid, selected)
    assert not (cfg.state_root / "knowledge-graph.sqlite3").exists()
    assert not (cfg.state_root / "graph-locks").exists()


def test_idempotency_and_partition(small, monkeypatch):
    cfg, cid, vid, selected, _, other, other_vault = small
    job = finish(small, monkeypatch)
    assert jobs.submit(cfg, cid, vid, selected)["id"] == job["id"]
    assert len(jobs.status(cfg, cid, vid)) == 1
    assert catalog.browse(cfg, other, other_vault)["items"] == []
    with pytest.raises(auth.Denied):
        jobs.status(cfg, other, other_vault, job["id"])
    with pytest.raises(auth.Denied):
        catalog.browse(cfg, cid, other_vault)


def test_rename_without_model_and_stale_support(small, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    finish(small, monkeypatch)
    before = catalog.browse(cfg, cid, vid)
    source = cfg.project_root / "test vault/temp_vault1/n0.md"
    source.rename(source.with_name("renamed.md"))
    after = catalog.browse(cfg, cid, vid)
    assert [x["id"] for x in before["items"]] == [x["id"] for x in after["items"]]
    assert any("renamed.md" in x["paths"].values() for x in after["items"])
    source = source.with_name("renamed.md")
    source.write_text(source.read_text() + "修改\n")
    stale = catalog.browse(cfg, cid, vid)
    assert all(x["review"]["state"] == "stale" and x["value"] is None for x in stale["items"])


def test_review_cas_rebuild_and_content_change(small, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    finish(small, monkeypatch)
    listing = catalog.browse(cfg, cid, vid)
    item = listing["items"][0]
    reviewed = catalog.review(
        cfg,
        cid,
        vid,
        item["id"],
        configuration=listing["configuration"],
        revision=0,
        state="accepted",
        reason="合成资料审核",
    )
    assert reviewed["revision"] == 1
    with pytest.raises(IntegrityError, match="revision"):
        catalog.review(
            cfg,
            cid,
            vid,
            item["id"],
            configuration=listing["configuration"],
            revision=0,
            state="rejected",
            reason="冲突",
        )
    with pytest.raises(auth.Denied):
        catalog.review(
            cfg,
            small[5],
            small[6],
            item["id"],
            configuration=listing["configuration"],
            revision=1,
            state="accepted",
            reason="越权",
        )
    path = cfg.project_root / "test vault/temp_vault1/n0.md"
    path.write_text(path.read_text() + "新增内容\n")
    with pytest.raises(IntegrityError, match="stale"):
        catalog.review(
            cfg,
            cid,
            vid,
            item["id"],
            configuration=listing["configuration"],
            revision=1,
            state="accepted",
            reason="旧来源",
        )
    new = list(selected)
    new[0] = replace(new[0], expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    job = jobs.submit(cfg, cid, vid, tuple(new))
    jobs.execute(cfg, cid, vid, job["id"])
    assert all(x["review"]["state"] == "suggested" for x in catalog.browse(cfg, cid, vid)["items"])
    with store.connection(cfg) as db:
        assert db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 1


@pytest.mark.parametrize(
    "failure", ["cancel", "revoke_restore", "edit", "profile", "timeout", "disk"]
)
def test_faults_never_replace_generation(small, monkeypatch, failure):
    cfg, cid, vid, selected, *_ = small
    first = finish(small, monkeypatch)
    # Different selection admits a new finite job; no auto retry of failed attempts.
    pending = jobs.submit(cfg, cid, vid, selected[:2])

    def fault(c, p, wp, cache, *, check, cancelled):
        if failure == "cancel":
            jobs.cancel(cfg, cid, vid, p.request.task_id)
        elif failure == "revoke_restore":
            db = database.connect(cfg.state_root)
            auth.set_grant(db, cid, vid, query=False, original=False, add=True)
            auth.set_grant(db, cid, vid, query=True, original=False, add=True)
            db.close()
        elif failure == "edit":
            target = cfg.project_root / "test vault/temp_vault1/n0.md"
            target.write_text(target.read_text() + "修改\n")
        elif failure == "profile":
            with database.locked(cfg):
                old = policy.current(cfg)
                policy.save(cfg, replace(old, revision=2, enabled=False), old.digest)
        elif failure == "timeout":
            raise InvalidInput("graph_worker_timeout")
        elif failure == "disk":
            raise OSError("no space")
        if cancelled():
            raise InvalidInput("graph_cancelled")
        check()
        return fake_build(c, p, wp, cache, check=check, cancelled=cancelled)

    monkeypatch.setattr(jobs.worker, "run_build", fault)
    with pytest.raises((IntegrityError, InvalidInput, OSError)):
        jobs.execute(cfg, cid, vid, pending["id"])
    with store.connection(cfg) as db:
        assert db.execute("SELECT job_id FROM generations").fetchone()[0] == first["id"]
        assert store.job(db, pending["id"], cid, vid)["state"] in {"failed", "cancelled"}
    assert (
        jobs.submit(cfg, cid, vid, selected[:2])["id"] == pending["id"]
        if failure in {"cancel", "timeout", "disk"}
        else True
    )


def test_crash_acknowledgement_and_restore_keeps_reviews(small, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    finish(small, monkeypatch)
    listing = catalog.browse(cfg, cid, vid)
    item = listing["items"][0]
    catalog.review(
        cfg,
        cid,
        vid,
        item["id"],
        configuration=listing["configuration"],
        revision=0,
        state="accepted",
        reason="保留决定",
    )
    job = jobs.submit(cfg, cid, vid, selected[:2])
    (cfg.state_root / "graph-locks" / f"{job['id']}.lock").touch()
    with store.connection(cfg, write=True) as db:
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
    monkeypatch.setattr(jobs.worker, "stop_abandoned", lambda *a: None)
    assert jobs.quarantine(cfg, cid, vid, job["id"])["state"] == "interrupted"
    result = backup_preflight.inspect(cfg)
    assert {"path": "knowledge-graph.sqlite3", "schema": 1} in result["databases"]
    restore_rebind._quarantine_sidecars(cfg)
    assert catalog.browse(cfg, cid, vid)["items"] == []
    with store.connection(cfg) as db:
        assert db.execute("SELECT state FROM reviews").fetchone()[0] == "accepted"
    assert jobs.execute(cfg, cid, vid, job["id"])["state"] == "interrupted"


def test_concurrent_claim_and_cancel_blocks_publish(small, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    cfg, cid, vid, selected, *_ = small
    first = jobs.submit(cfg, cid, vid, selected)
    second = jobs.submit(cfg, cid, vid, selected[:2])
    admitted, release = Event(), Event()

    def pause(c, p, wp, cache, *, check, cancelled):
        admitted.set()
        assert release.wait(5)
        if cancelled():
            raise InvalidInput("graph_cancelled")
        return fake_build(c, p, wp, cache, check=check, cancelled=cancelled)

    monkeypatch.setattr(jobs.worker, "run_build", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(jobs.execute, cfg, cid, vid, first["id"])
        assert admitted.wait(5)
        with pytest.raises(InvalidInput, match="busy"):
            jobs.execute(cfg, cid, vid, second["id"])
        jobs.cancel(cfg, cid, vid, first["id"])
        release.set()
        with pytest.raises(InvalidInput, match="cancelled"):
            task.result(5)
    with store.connection(cfg) as db:
        assert not db.execute("SELECT * FROM generations").fetchall()


def test_publication_disk_failure_rolls_back_pointer(small, monkeypatch):
    from contextlib import contextmanager

    cfg, cid, vid, selected, *_ = small
    first = finish(small, monkeypatch)
    second = jobs.submit(cfg, cid, vid, selected[:2])
    original = store.connection
    fail = [False]

    def built(*args, **kwargs):
        value = fake_build(*args, **kwargs)
        fail[0] = True
        return value

    @contextmanager
    def disk(config, *, write=False):
        if write and fail[0]:
            fail[0] = False
            raise sqlite3.OperationalError("synthetic disk failure")
        with original(config, write=write) as db:
            yield db

    monkeypatch.setattr(jobs.worker, "run_build", built)
    monkeypatch.setattr(store, "connection", disk)
    with pytest.raises(sqlite3.OperationalError):
        jobs.execute(cfg, cid, vid, second["id"])
    with original(cfg) as db:
        assert db.execute("SELECT job_id FROM generations").fetchone()[0] == first["id"]
        assert store.job(db, second["id"], cid, vid)["state"] == "failed"


def test_live_lease_cannot_be_quarantined(small, monkeypatch):
    import fcntl

    cfg, cid, vid, selected, *_ = small
    job = jobs.submit(cfg, cid, vid, selected)
    (cfg.state_root / "graph-locks").mkdir()
    path = cfg.state_root / "graph-locks" / f"{job['id']}.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.connection(cfg, write=True) as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
        monkeypatch.setattr(
            jobs.worker, "stop_abandoned", lambda *a: pytest.fail("live owner must not be stopped")
        )
        with pytest.raises(InvalidInput, match="still_running"):
            jobs.quarantine(cfg, cid, vid, job["id"])
        with pytest.raises(InvalidInput):
            backup_preflight.inspect(cfg)
    finally:
        os.close(fd)


def test_model_cache_binding_and_invalid_numbers(small):
    cfg, _, vid, selected, token, *_ = small
    model = Model("synthetic", "v1", "1" * 64, 3)
    prepared = intake.prepare(cfg, token, vid, selected, Engine(model=model), enabled=True)
    request = prepared.request
    cache = vectors.rows(request, {n.note_id: [1.0, 0.0, 0.0] for n in request.notes})
    decoded, values = vectors.decode(vectors.encode(request, cache))
    assert decoded == request and len(values) == 3
    for value in [True, float("nan"), float("inf"), 2.0]:
        invalid = json.loads(canonical(cache))
        next(iter(invalid.values()))["values"][0] = value
        with pytest.raises(InvalidInput):
            vectors.validate(invalid, request)
    changed = replace(request, engine=replace(request.engine, model=replace(model, revision="v2")))
    with pytest.raises(InvalidInput):
        vectors.validate(cache, changed)


def test_old_generation_permission_epoch_is_not_revived(small, monkeypatch):
    cfg, cid, vid, *_ = small
    finish(small, monkeypatch)
    db = database.connect(cfg.state_root)
    auth.set_grant(db, cid, vid, query=False, original=False, add=True)
    auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    db.close()
    assert catalog.browse(cfg, cid, vid)["state"] == "unavailable"


def test_algorithm_upgrade_preserves_history_without_reusing_vectors_or_approval(
    small, monkeypatch
):
    cfg, cid, vid, selected, token, *_ = small
    finish(small, monkeypatch)
    listing = catalog.browse(cfg, cid, vid)
    item = listing["items"][0]
    catalog.review(
        cfg,
        cid,
        vid,
        item["id"],
        configuration=listing["configuration"],
        revision=0,
        state="accepted",
        reason="旧配置审核",
    )
    old = policy.current(cfg)
    changed = replace(
        old,
        revision=old.revision + 1,
        image="sha256:" + "2" * 64,
        engine=replace(old.engine, algorithm="body_topics_v5"),
    )
    with database.locked(cfg):
        policy.save(cfg, changed, old.digest)
    assert catalog.browse(cfg, cid, vid)["state"] == "unavailable"
    with store.connection(cfg) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM reviews WHERE state=?", ("accepted",)).fetchone()[0]
            == 1
        )
    job = jobs.submit(cfg, cid, vid, selected)
    jobs.execute(cfg, cid, vid, job["id"])
    new = catalog.browse(cfg, cid, vid)
    assert new["configuration"] != listing["configuration"]
    assert all(i["review"]["state"] == "suggested" for i in new["items"])
    model = Model("synthetic", "v1", "1" * 64, 3)
    request = intake.prepare(cfg, token, vid, selected, Engine(model=model), enabled=True).request
    cache = vectors.rows(request, {n.note_id: [1.0, 0.0, 0.0] for n in request.notes})
    with pytest.raises(InvalidInput):
        vectors.validate(
            cache, replace(request, engine=replace(request.engine, algorithm="body_topics_v5"))
        )
