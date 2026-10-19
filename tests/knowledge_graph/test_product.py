import hashlib
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fiber_bundle import auth, database, moc_file_journal, moc_files, moc_store
from fiber_bundle.api import create_app
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.knowledge_graph import catalog, jobs, retrieval, vectors, writeback

from .support import result, span
from .test_lifecycle import small as small


def moc_build(c, p, wp, cache, *, check, cancelled):
    check()
    value = result(p.request)
    for selected in [p.request.notes[:2], p.request.notes[1:]]:
        value["mocs"].append(
            {
                "title": "合成资料关联组",
                "note_ids": sorted(n.note_id for n in selected),
                "evidence": [asdict(span(n, n.title)) for n in selected],
                "method": p.request.engine.algorithm,
            }
        )
    raw = canonical(value).encode()
    return vectors.Build(catalog.decode_result(raw, p.request), raw, {}, 0)


@pytest.fixture
def candidates(small, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    monkeypatch.setattr(jobs.worker, "run_build", moc_build)
    job = jobs.submit(cfg, cid, vid, selected)
    jobs.execute(cfg, cid, vid, job["id"])
    listing = catalog.browse(cfg, cid, vid)
    mocs = [i for i in listing["items"] if i["type"] == "moc"]
    for item in mocs:
        catalog.review(
            cfg,
            cid,
            vid,
            item["id"],
            configuration=listing["configuration"],
            revision=0,
            state="accepted",
            reason="合成来源逐项核对",
        )
    return mocs


def test_moc_preview_preserves_manual_body_and_idempotent_apply(small, candidates):
    cfg, cid, vid, *_ = small
    target = cfg.project_root / "test vault/temp_vault1/moc.md"
    manual = "# 人工目录\n\n人工正文不要改变。\n- 人工链接\n"
    target.write_text(manual)
    mid = str(uuid4())
    oid = candidates[0]["id"]
    preview = writeback.prepare(
        cfg,
        cid,
        vid,
        oid,
        moc_id=mid,
        revision=0,
        relative="moc.md",
        expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    assert target.read_text() == manual and moc_store.load(cfg, vid, mid) is None
    assert manual in preview["markdown"]
    with pytest.raises(InvalidInput):
        writeback.apply(cfg, cid, vid, preview["id"])
    applied = writeback.apply(cfg, cid, vid, preview["id"], confirm=True)
    assert applied["state"] == "completed" and manual in target.read_text()
    after = target.read_bytes()
    revision = moc_store.load(cfg, vid, mid).revision
    assert writeback.apply(cfg, cid, vid, preview["id"], confirm=True)["id"] == applied["id"]
    assert target.read_bytes() == after and moc_store.load(cfg, vid, mid).revision == revision
    assert (
        writeback.prepare(
            cfg,
            cid,
            vid,
            oid,
            moc_id=mid,
            revision=revision,
            relative="moc.md",
            expected_sha256=hashlib.sha256(after).hexdigest(),
        )["id"]
        == applied["id"]
    )


def test_shared_system_contributions_and_manual_owners_survive_undo(small, candidates):
    cfg, cid, vid, *_ = small
    mid = str(uuid4())
    path = cfg.project_root / "test vault/temp_vault1/shared.md"
    ids = [item["value"]["note_ids"] for item in candidates]
    shared = next(iter(set(ids[0]) & set(ids[1])))
    root = path.parent.stat()
    manual = moc_store.Record("人工集合", root.st_dev, root.st_ino, 1, {shared: ["manual"]})
    with database.locked(cfg):
        moc_store.save(cfg, vid, mid, cid, manual, "create", 0)
    first = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=mid,
        revision=1,
        relative=path.name,
        expected_sha256="absent",
    )
    writeback.apply(cfg, cid, vid, first["id"], confirm=True)
    second = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[1]["id"],
        moc_id=mid,
        revision=2,
        relative=path.name,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    writeback.apply(cfg, cid, vid, second["id"], confirm=True)
    undo = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=mid,
        revision=3,
        relative=path.name,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        undo=first["id"],
    )
    writeback.apply(cfg, cid, vid, undo["id"], confirm=True)
    current = moc_store.load(cfg, vid, mid)
    assert current.members[shared] == ["manual", "system"]
    undo2 = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[1]["id"],
        moc_id=mid,
        revision=4,
        relative=path.name,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        undo=second["id"],
    )
    writeback.apply(cfg, cid, vid, undo2["id"], confirm=True)
    assert moc_store.load(cfg, vid, mid).members == {shared: ["manual"]}


@pytest.mark.parametrize("change", ["source", "review", "file", "revision", "permission"])
def test_writeback_stale_contract_blocks_write(small, candidates, change):
    cfg, cid, vid, *_ = small
    mid = str(uuid4())
    path = cfg.project_root / "test vault/temp_vault1/moc.md"
    selected = candidates[0]
    preview = writeback.prepare(
        cfg,
        cid,
        vid,
        selected["id"],
        moc_id=mid,
        revision=0,
        relative=path.name,
        expected_sha256="absent",
    )
    if change == "source":
        note = next(iter(selected["paths"].values()))
        target = path.parent / note
        target.write_text(target.read_text() + "更新\n")
    elif change == "review":
        listing = catalog.browse(cfg, cid, vid)
        catalog.review(
            cfg,
            cid,
            vid,
            selected["id"],
            configuration=listing["configuration"],
            revision=1,
            state="rejected",
            reason="重新判断",
        )
    elif change == "file":
        path.write_text("# 新人工正文\n")
    elif change == "revision":
        root = path.parent.stat()
        with database.locked(cfg):
            moc_store.save(
                cfg,
                vid,
                mid,
                cid,
                moc_store.Record("手工建立", root.st_dev, root.st_ino, 1, {}),
                "create",
                0,
            )
    elif change == "permission":
        db = database.connect(cfg.state_root)
        auth.set_grant(db, cid, vid, query=True, original=False, add=False)
        db.close()
    before = path.read_bytes() if path.exists() else None
    with pytest.raises((IntegrityError, auth.Denied)):
        writeback.apply(cfg, cid, vid, preview["id"], confirm=True)
    assert (path.read_bytes() if path.exists() else None) == before


def test_file_commit_interruption_reconciles_without_replay(small, candidates, monkeypatch):
    cfg, cid, vid, *_ = small
    mid = str(uuid4())
    path = cfg.project_root / "test vault/temp_vault1/moc.md"
    preview = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=mid,
        revision=0,
        relative=path.name,
        expected_sha256="absent",
    )
    original = writeback._update

    def interrupt(config, wid, state, value):
        if state == "completed":
            raise OSError("synthetic crash after actual file commit")
        return original(config, wid, state, value)

    monkeypatch.setattr(writeback, "_update", interrupt)
    with pytest.raises(OSError):
        writeback.apply(cfg, cid, vid, preview["id"], confirm=True)
    assert path.exists()
    frozen = path.read_bytes()
    plans = moc_file_journal.plans(cfg, vid, mid)
    assert plans[0].status == "applied"
    with pytest.raises(IntegrityError, match="reconciliation"):
        writeback.apply(cfg, cid, vid, preview["id"], confirm=True)
    monkeypatch.setattr(writeback, "_update", original)
    assert writeback.reconcile(cfg, cid, vid, preview["id"])["state"] == "completed"
    assert path.read_bytes() == frozen and len(moc_file_journal.plans(cfg, vid, mid)) == 1


def test_unclaimed_file_plan_after_crash_is_not_replayed(small, candidates, monkeypatch):
    cfg, cid, vid, *_ = small
    mid = str(uuid4())
    path = cfg.project_root / "test vault/temp_vault1/moc.md"
    preview = writeback.prepare(
        cfg,
        cid,
        vid,
        candidates[0]["id"],
        moc_id=mid,
        revision=0,
        relative=path.name,
        expected_sha256="absent",
    )
    monkeypatch.setattr(
        moc_files,
        "apply_file",
        lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic crash before claiming file")),
    )
    with pytest.raises(OSError):
        writeback.apply(cfg, cid, vid, preview["id"], confirm=True)
    with pytest.raises(IntegrityError, match="no_replay"):
        writeback.reconcile(cfg, cid, vid, preview["id"])
    assert not path.exists()


def test_literal_default_and_bounded_semantic_clues(small, candidates, monkeypatch):
    cfg, cid, vid, *_ = small
    monkeypatch.setattr(
        retrieval.worker, "run_query", lambda *a, **k: [0.9] * len(a[1].request.notes)
    )
    baseline = retrieval.recall(cfg, cid, vid, "散热板")
    assert baseline["graph_state"] == "disabled" and baseline["graph_candidates"] == []
    graph = retrieval.recall(cfg, cid, vid, "散热板", enabled=True)
    assert len(graph["graph_candidates"]) <= 10 and all(
        row["evidence_kind"] == "retrieval_clue" for row in graph["graph_candidates"]
    )
    assert graph["results"] == baseline["results"]
    # Technical IDs absent from sources must not gain a semantic match.
    graph = retrieval.recall(cfg, cid, vid, "AB-123", enabled=True)
    assert graph["graph_candidates"] == []


def test_fallback_rechecks_permission_and_current_evidence(small, candidates, monkeypatch):
    cfg, cid, vid, *_ = small

    def revoke(*a, **k):
        db = database.connect(cfg.state_root)
        auth.set_grant(db, cid, vid, query=False, original=False, add=True)
        db.close()
        raise InvalidInput("worker unavailable")

    monkeypatch.setattr(retrieval.worker, "run_query", revoke)
    with pytest.raises(auth.Denied):
        retrieval.recall(cfg, cid, vid, "青松", enabled=True)


def test_http_assets_auth_csrf_and_scoped_actions(small, candidates, monkeypatch):
    cfg, cid, vid, selected, *_ = small
    auth.set_password(cfg, "synthetic-graph-admin-password")
    http = TestClient(
        create_app(cfg), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 20000)
    )
    base = "/admin/knowledge-graph"
    query = f"?client_id={cid}&vault_id={vid}"
    assert http.get(base + "/suggestions" + query).status_code == 401
    http.headers["Origin"] = "http://127.0.0.1:8765"
    login = http.post("/admin/login", json={"password": "synthetic-graph-admin-password"})
    assert login.status_code == 200
    http.headers["X-CSRF-Token"] = login.json()["csrf"]
    assert http.get(base + "/suggestions" + query).json()["state"] == "ready"
    for name in ["knowledge_graph.js", "knowledge_graph_api.js", "knowledge_graph_moc.js"]:
        assert http.get("/static/" + name).status_code == 200
    data = {"client_id": cid, "vault_id": vid, "selected": [asdict(s) for s in selected[:2]]}
    new = http.post(base + "/jobs", json=data)
    assert new.status_code == 202, new.text
    jid = new.json()["id"]
    assert (
        http.post(
            base + "/jobs/" + jid + "/cancel", json={"client_id": cid, "vault_id": vid}
        ).status_code
        == 200
    )
    http.headers.pop("X-CSRF-Token")
    assert http.post(base + "/jobs", json=data).status_code == 403
    assert not http.get("/static/worker_entry.py").status_code == 200
    http.close()
