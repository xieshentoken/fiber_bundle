"""Manual choices bypass model failures and preserve all prior ownership."""

import pytest
from test_jobs import ready as ready
from test_moc_recovery import historical_map

from fiber_bundle import auth, database, moc_file_journal, moc_markdown, moc_store
from fiber_bundle.organization import (
    jobs,
    membership,
    note_moc_fields,
    note_moc_sync,
    packages,
    store,
)


def selection(config, ready):
    cid, vid, root, _, _ = ready
    (root / "Map/材料 MOC.md").write_text("---\ntype: moc\n---\n# 材料 MOC\n人工正文\n")
    with database.connect(config.state_root) as db:
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
    return {
        "client_id": cid,
        "vault_id": vid,
        "entity_id": entities[0]["id"],
        "moc_ids": [m["id"] for m in mocs],
        "action": "link",
    }


def test_batch_links_two_maps_preserves_properties_and_is_idempotent(config, ready, http):
    cid, vid, _, moc, note = ready
    note.write_text("---\ntitle: 手动标题\ncustom: 42\n---\n正文\n")
    request = selection(config, ready)
    response = http.post("/admin/organization/members/batch", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["completed"] and len(response.json()["results"]) == 2
    assert set(note_moc_fields.field(note.read_text())[2]) == {
        "[[Map/材料 MOC]]",
        "[[Map/热管理 MOC]]",
    }
    assert "custom: 42" in note.read_text() and "title: 手动标题" in note.read_text()
    before = note.read_bytes(), moc.read_bytes()
    assert not http.post("/admin/organization/members/batch", json=request).json()["changed"]
    assert before == (note.read_bytes(), moc.read_bytes())
    one = {**request, "moc_ids": [request["moc_ids"][0]], "action": "unlink"}
    assert http.post("/admin/organization/members/batch", json=one).json()["completed"]
    assert len(note_moc_fields.field(note.read_text())[2]) == 1
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=False)
    assert http.post("/admin/organization/members/batch", json=request).status_code == 404


def test_manual_recovers_absent_map_block_and_legacy_partial_relation(config, ready, http):
    cid, vid, _, moc, note = ready
    with database.connect(config.state_root) as db:
        mid, old, human = historical_map(config, db, ready)
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        entity = next(e for e in entities if e["path"] == "Inbox/传热.md")
        # Reproduce the old route: metadata committed, then missing block rejected.
        with pytest.raises(Exception, match="managed block missing"):
            membership.apply(config, db, cid, vid, entity, mocs[0], owner="manual")
    response = http.post(
        "/admin/organization/members",
        json={
            "client_id": cid,
            "vault_id": vid,
            "entity_id": entity["id"],
            "moc_id": mid,
            "action": "link",
        },
    )
    assert response.status_code == 200 and response.json()["completed"], response.text
    prefix, block, suffix = moc_markdown.split(moc.read_bytes(), mid)
    assert prefix + suffix == human and "传热" in block.decode() and "旧笔记" in block.decode()
    assert moc_store.load(config, vid, mid).members[old] == ["manual"]
    assert note_moc_fields.field(note.read_text())[2] == ["[[Map/热管理 MOC]]"]


def test_changed_managed_block_is_reported_before_any_new_decision(config, ready, http):
    cid, vid, root, moc, _ = ready
    request = selection(config, ready)
    assert http.post("/admin/organization/members/batch", json=request).json()["completed"]
    moc.write_text(moc.read_text().replace("系统增量关联", "人工修改系统区块"))
    fresh = root / "Inbox/新笔记.md"
    fresh.write_text("# 新笔记\n")
    with database.connect(config.state_root) as db:
        entities, _, _ = jobs.snapshot(config, db, cid, vid)
    request["entity_id"] = next(e["id"] for e in entities if e["path"] == "Inbox/新笔记.md")
    before = store.rows(config, "decisions"), fresh.read_bytes(), moc.read_bytes()
    result = http.post("/admin/organization/members/batch", json=request).json()
    assert not result["completed"] and not result["changed"]
    assert all(r["error"] == "maintenance_moc_managed_block_edited" for r in result["results"])
    assert before == (store.rows(config, "decisions"), fresh.read_bytes(), moc.read_bytes())


def test_partial_write_reports_each_target_and_retry_finishes_without_duplicate(
    config, ready, http, monkeypatch
):
    request = selection(config, ready)
    original = note_moc_sync.sync
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(note_moc_sync, "sync", fail_once)
    result = http.post("/admin/organization/members/batch", json=request).json()
    assert [r["completed"] for r in result["results"]] == [False, True]
    monkeypatch.setattr(note_moc_sync, "sync", original)
    result = http.post("/admin/organization/members/batch", json=request).json()
    assert result["completed"] and not result["changed"]
    assert len(note_moc_fields.field(ready[4].read_text())[2]) == 2
    for mid in request["moc_ids"]:
        assert moc_store.load(config, ready[1], mid).members[request["entity_id"]] == ["manual"]


def test_invalid_selection_preflight_preserves_notes(http, config, ready):
    request = selection(config, ready)
    request["moc_ids"].append("missing")
    before = ready[4].read_bytes()
    assert http.post("/admin/organization/members/batch", json=request).status_code == 422
    assert ready[4].read_bytes() == before


def test_additional_choices_preserve_existing_manual_membership(config, ready, http):
    request = selection(config, ready)
    first = {**request, "moc_ids": request["moc_ids"][:1]}
    assert http.post("/admin/organization/members/batch", json=first).json()["completed"]
    request["moc_ids"] = request["moc_ids"][1:] * 2
    result = http.post("/admin/organization/members/batch", json=request).json()
    assert result["completed"] and len(result["results"]) == 1
    assert len(note_moc_fields.field(ready[4].read_text())[2]) == 2
    assert len(store.rows(config, "decisions")) == 2


def test_expert_multi_mount_never_rewrites_package(config, ready, package, http):
    request = selection(config, ready)
    before = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    with database.connect(config.state_root) as db:
        registered = packages.register(config, db, ready[0], ready[1], "Book/fake-thermal")
    request["entity_id"] = registered["module_id"]
    response = http.post("/admin/organization/members/batch", json=request)
    assert response.status_code == 200 and response.json()["completed"], response.text
    assert before == {
        p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()
    }
    assert len(store.rows(config, "decisions")) == 2


def test_retry_reconciles_exact_map_exchange_after_interruption(config, ready, http, monkeypatch):
    request = selection(config, ready)
    assert http.post("/admin/organization/members/batch", json=request).json()["completed"]
    note = ready[2] / "Inbox/中断恢复.md"
    note.write_text("# 中断恢复\n原文不改\n")
    with database.connect(config.state_root) as db:
        entities, _, _ = jobs.snapshot(config, db, ready[0], ready[1])
    request["entity_id"] = next(e["id"] for e in entities if e["path"] == "Inbox/中断恢复.md")
    original = moc_file_journal.finish
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("interrupted after Map exchange")
        return original(*args, **kwargs)

    monkeypatch.setattr(moc_file_journal, "finish", fail_once)
    first = http.post("/admin/organization/members/batch", json=request).json()
    assert not first["completed"], first
    monkeypatch.setattr(moc_file_journal, "finish", original)
    again = http.post("/admin/organization/members/batch", json=request).json()
    assert again["completed"] and not again["changed"], again
    assert len(note_moc_fields.field(note.read_text())[2]) == 2
    assert note.read_text().endswith("# 中断恢复\n原文不改\n")
