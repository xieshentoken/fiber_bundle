from test_jobs import ready as ready
from test_maintenance import preview
from test_maintenance_apply import execute

from fiber_bundle import database, moc_store
from fiber_bundle.organization import (
    jobs,
    maintenance,
    maintenance_analysis,
    note_moc_fields,
    routes,
    store,
)


def wrong_membership(ready):
    cid, vid, root, moc, note = ready
    wrong = root / "Map/数学 MOC.md"
    wrong.write_text("---\ntype: moc\n---\n# 数学 MOC\n\n人工引文：[[Inbox/传热]]\n")
    note.write_text("---\nmoc: '[[数学 MOC]]'\n---\n# 传热\n#热管理\n傅里叶定律。\n")
    return wrong, note


def test_deep_replaces_ordinary_human_membership_keeps_prose_and_does_not_resurrect(config, ready):
    cid, vid, root, _, _ = ready
    wrong, note = wrong_membership(ready)
    before = note.read_bytes()
    manual = wrong.read_bytes()
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "deep")
        old = job["items"][0]["remove"][0]
        result = execute(config, db, job)
        assert result["state"] == "completed", result
        assert note_moc_fields.field(note.read_text())[2] == ["[[数学 MOC]]", "[[Map/热管理 MOC]]"]
        assert note.read_bytes().endswith(before.split(b"---\n", 2)[2])
        assert wrong.read_bytes().startswith(manual)
        second = preview(config, db, cid, vid, "deep")
        assert old not in second["items"][0]["before"]
        assert not second["items"][0]["add"] and not second["items"][0]["remove"]
        jobs.run(config, db, jobs.submit(config, db, cid, vid, "全库"))
        assert not moc_store.load(config, vid, old).members
        visible = routes.browse(config, db, cid, vid, exceptions=False)
        assert old not in visible["items"][0]["moc_ids"]


def test_late_manual_lock_blocks_all_writes_after_confirmation(config, ready):
    cid, vid, root, moc, _ = ready
    wrong, _ = wrong_membership(ready)
    before = moc.read_bytes(), wrong.read_bytes()
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "deep")
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        entity = job["items"][0]["entity"]
        old = job["items"][0]["remove"][0]
        store.put(
            config,
            "decisions",
            vid + ":" + entity + ":" + old,
            vid,
            {"entity": entity, "moc": old, "state": "pinned"},
        )
        result = maintenance.run(config, db, job)
        assert result["state"] == "partial" and "manual_decision_changed" in result["error"]
    assert (moc.read_bytes(), wrong.read_bytes()) == before


def test_unknown_new_classification_retains_old_relationship(config, ready):
    cid, vid, _, _, note = ready
    note.write_text("---\nmoc: '[[热管理 MOC]]'\n---\n# 不确定资料\n短文。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "deep")
    maintenance_analysis.prepare(config, job["id"])
    with database.connect(config.state_root) as db:
        job = maintenance.run(config, db, store.get(config, "jobs", job["id"]))
        assert job["state"] == "preview"
        item = job["items"][0]
        assert item["before"] == item["after"] and not item["remove"]


def test_cancellation_never_applies_even_if_plan_is_ready(config, ready):
    cid, vid, _, moc, _ = ready
    before = moc.read_bytes()
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "cancel", None)
        assert maintenance.run(config, db, job)["state"] == "cancelled"
    assert moc.read_bytes() == before


def test_deep_recovers_after_relation_write_without_readding_inherited_link(config, ready):
    from unittest.mock import patch

    from fiber_bundle.organization import membership

    cid, vid, *_ = ready
    wrong_membership(ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "deep")
        old = job["items"][0]["remove"][0]
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        original = membership.apply

        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("action") == "unlink":
                raise OSError("after committed membership")
            return result

        with patch.object(membership, "apply", interrupted):
            failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial"
        assert (
            next(d for d in store.rows(config, "decisions", vid) if d["moc"] == old)["state"]
            == "retired"
        )
        resumed = maintenance.action(config, db, failed, "resume", None)
        result = maintenance.run(config, db, resumed)
        assert result["state"] == "completed", result
        assert not moc_store.load(config, vid, old).members
