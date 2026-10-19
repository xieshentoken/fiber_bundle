"""Large read-only overviews must not re-evaluate all MOCs for every MOC."""

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient
from test_jobs import ready as ready

from fiber_bundle import auth, database
from fiber_bundle.api import create_app
from fiber_bundle.knowledge_graph import policy
from fiber_bundle.knowledge_graph.contracts import Engine
from fiber_bundle.organization import jobs, overview, store


@pytest.fixture
def large_overview(ready):
    cid, vid, root, heat, note = ready
    note.write_text("# 传热\n[[Map/热管理 MOC]]\n傅里叶定律。\n")
    for i in range(25):
        (root / f"Map/主题{i:02d} MOC.md").write_text(
            f"# 主题{i:02d} MOC\n\n人工主题定义{i}，原样保留。\n"
        )
    for i in range(532):
        (root / f"Inbox/资料{i:03d}.md").write_text(
            f"# 资料{i}\n\n[[Map/主题{i % 25:02d} MOC]]\n这是正文资料{i}。\n"
        )
    return cid, vid, root


def test_large_overview_reads_membership_and_decisions_once(config, large_overview, monkeypatch):
    cid, vid, root = large_overview
    before = {p: p.read_bytes() for p in root.rglob("*.md")}
    membership_reads, decision_reads = [], []
    from fiber_bundle.organization.manual_relations import ManualRelations

    original, rows = ManualRelations.memberships, store.rows

    def membership(index, entity):
        membership_reads.append(entity["id"])
        return original(index, entity)

    def decisions(*args, **kwargs):
        if args[1] == "decisions":
            decision_reads.append(args[1])
        return rows(*args, **kwargs)

    monkeypatch.setattr(ManualRelations, "memberships", membership)
    monkeypatch.setattr(store, "rows", decisions)
    with database.connect(config.state_root) as db:
        count = db.total_changes
        value = overview.browse(config, db, cid, vid)
        assert db.total_changes == count
    assert value["items"] == [] and value["total"] == 0
    assert len(value["mocs"]) == 26
    assert len(membership_reads) == len(set(membership_reads)) == 533
    assert decision_reads == ["decisions"]
    assert not (config.state_root / "organization.sqlite3").exists()
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == before


def test_overview_preserves_manual_backlinks_rejections_and_filters(config, ready):
    cid, vid, root, heat, note = ready
    note.write_text("# 传热\n[[Map/热管理 MOC]]\n")
    other = root / "Map/材料 MOC.md"
    other.write_text("# 材料 MOC\n\n人工关联：[[Inbox/传热]]\n")
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        entity = entities[0]["id"]
        mids = {m["title"]: m["id"] for m in mocs}
        visible = overview.browse(config, db, cid, vid, exceptions=False)
        assert set(visible["items"][0]["moc_ids"]) == set(mids.values())
        assert visible["items"][0]["status"] == "linked"
        for i, (title, state) in enumerate([("热管理 MOC", "rejected"), ("材料 MOC", "retired")]):
            store.put(
                config,
                "decisions",
                str(i),
                vid,
                {"entity": entity, "moc": mids[title], "state": state},
            )
            visible = overview.browse(config, db, cid, vid, exceptions=False)
            assert mids[title] not in visible["items"][0]["moc_ids"]
        assert visible["items"][0]["status"] == "new"
        assert overview.browse(config, db, cid, vid)["total"] == 1
        assert overview.browse(config, db, cid, vid, query="无关标题")["total"] == 0
        assert overview.browse(config, db, cid, vid, moc_id=mids["材料 MOC"])["total"] == 0
        assert overview.browse(config, db, cid, vid, offset=1)["items"] == []
    assert "人工关联" in other.read_text()


def test_enable_local_analysis_then_read_large_scope_and_health(config, large_overview):
    cid, vid, _ = large_overview
    auth.set_password(config, "synthetic-graph-overview-password")
    app = create_app(config)
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 20000)) as client:
        client.headers["Origin"] = "http://127.0.0.1:8765"
        response = client.post(
            "/admin/login", json={"password": "synthetic-graph-overview-password"}
        )
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        value = policy.Profile(1, True, "sha256:" + "1" * 64, Engine())
        saved = client.put(
            "/admin/knowledge-graph/policy", json={"expected_sha256": None, "value": asdict(value)}
        )
        assert saved.status_code == 200 and saved.json()["enabled"]
        query = {"client_id": cid, "vault_id": vid}
        response = client.get("/admin/organization", params=query)
        assert response.status_code == 200 and response.json()["total"] == 0
        for path in ["notes", "suggestions", "jobs"]:
            assert client.get("/admin/knowledge-graph/" + path, params=query).status_code == 200
        assert client.get("/health").status_code == 200
    assert policy.current(config) == value
    # Enabling an environment and reading the page must never create an analysis job.
    assert not (config.state_root / "knowledge-graph.sqlite3").exists()
