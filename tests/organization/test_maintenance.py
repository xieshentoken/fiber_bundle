import pytest
from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import jobs, maintenance, store


def preview(config, db, cid, vid, mode, **kwargs):
    job = maintenance.submit(config, db, cid, vid, mode, **kwargs)
    assert all(i["evaluated"] for i in job["items"]), job
    return maintenance.run(config, db, job)


def test_preview_has_no_file_side_effect_and_new_moc_waits_for_confirmation(config, ready):
    cid, vid, root, moc, note = ready
    before = {str(p): p.read_bytes() for p in [moc, note]}
    with database.connect(config.state_root) as db:
        p = preview(
            config,
            db,
            cid,
            vid,
            "quick",
            new_mocs=[{"title": "新主题", "topic": "与现有热管理不同的独立主题"}],
        )
        assert p["state"] == "preview" and p["plan_sha256"]
        assert p["items"][0]["target"] == "Resource/传热.md"
        with pytest.raises(InvalidInput, match="confirmation_required"):
            maintenance.action(config, db, p, "confirm", None)
        assert not (root / "Map/新主题 MOC.md").exists()
    assert all(__import__("pathlib").Path(n).read_bytes() == b for n, b in before.items())


def test_full_includes_classified_notes_and_deep_replaces_unlocked_manual(config, ready):
    cid, vid, root, _, note = ready
    wrong = root / "Map/数学 MOC.md"
    wrong.write_text("---\ntype: moc\n---\n# 数学 MOC\n")
    note.write_text("---\nmoc: '[[数学 MOC]]'\n---\n# 传热\n#热管理\n傅里叶定律。\n")
    with database.connect(config.state_root) as db:
        full = preview(config, db, cid, vid, "additive")
        assert len(full["items"]) == 1
        item = full["items"][0]
        assert len(item["before"]) == 1 and len(item["after"]) == 2
        assert not item["remove"] and not item["target"]
        deep = preview(config, db, cid, vid, "deep")
        assert deep["items"][0]["remove"] == item["before"]
        assert not deep["items"][0]["target"]


def test_deep_keeps_locks_and_rejections(config, ready):
    cid, vid, root, _, _ = ready
    (root / "Map/数学 MOC.md").write_text("---\ntype: moc\n---\n# 数学 MOC\n")
    with database.connect(config.state_root) as db:
        es, ms, _ = jobs.snapshot(config, db, cid, vid)
        thermal = next(m for m in ms if "热管理" in m["title"])
        math = next(m for m in ms if "数学" in m["title"])
        for m, state in [(thermal, "rejected"), (math, "pinned")]:
            store.put(
                config,
                "decisions",
                vid + ":" + es[0]["id"] + ":" + m["id"],
                vid,
                {"entity": es[0]["id"], "moc": m["id"], "state": state},
            )
        job = maintenance.submit(config, db, cid, vid, "deep")
        assert thermal["id"] not in job["items"][0]["after"]
        assert math["id"] in job["items"][0]["after"]


def test_preview_source_and_policy_conflicts(config, ready):
    cid, vid, _, _, note = ready
    with database.connect(config.state_root) as db:
        p = preview(config, db, cid, vid, "quick")
        note.write_text(note.read_text() + "edited")
        with pytest.raises(IntegrityError, match="preview_changed"):
            maintenance.action(config, db, p, "confirm", p["plan_sha256"])


def test_quick_prunes_non_inbox_and_templates_before_read(config, ready, monkeypatch):
    from fiber_bundle import note_index

    cid, vid, root, _, _ = ready
    outside = root / "Resource/private.md"
    outside.write_text("# private\n#热管理\n")
    folder = root / "Templates"
    folder.mkdir()
    (folder / "secret.md").write_text("#热管理")
    seen = []
    original = note_index.read_note

    def track(root, path, limit):
        seen.append(path)
        return original(root, path, limit)

    monkeypatch.setattr(note_index, "read_note", track)
    with database.connect(config.state_root) as db:
        p = preview(config, db, cid, vid, "quick")
        assert len(p["items"]) == 1
        maintenance.submit(config, db, cid, vid, "additive")
    assert "Templates/secret.md" not in seen
    assert seen.count("Resource/private.md") == 1


def test_maintenance_http_requires_bound_digest(http, config, ready):
    cid, vid, *_ = ready
    scope = {"client_id": cid, "vault_id": vid}
    r = http.post("/admin/organization/maintenance", json={**scope, "mode": "deep"})
    assert r.status_code == 200, r.text
    job = r.json()
    with database.connect(config.state_root) as db:
        maintenance.run(config, db, store.get(config, "jobs", job["id"]))
    r = http.post(f"/admin/organization/maintenance/{job['id']}/confirm", json=scope)
    assert r.status_code == 422, r.text
    assert (
        http.post("/admin/organization/maintenance", json={**scope, "mode": "unknown"}).status_code
        == 422
    )
