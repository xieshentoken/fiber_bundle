from unittest.mock import patch

from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import database, moc_store
from fiber_bundle.organization import jobs, maintenance, note_moc_fields, packages, store


def execute(config, db, job):
    job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    for _ in range(len(job["items"]) + 2):
        job = maintenance.run(config, db, job)
        if job["state"] in {"completed", "partial"}:
            break
    return job


def test_quick_moves_plain_note_updates_links_and_preserves_identity(config, ready):
    cid, vid, root, moc, note = ready
    source = note.read_bytes()
    other = root / "Resource/reference.md"
    other.write_text("# 引用\n[[Inbox/传热]]\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        identity = job["items"][0]["entity"]
        result = execute(config, db, job)
        assert result["state"] == "completed", result
        assert not note.exists() and (root / "Resource/传热.md").read_bytes().endswith(source)
        assert note_moc_fields.field((root / "Resource/传热.md").read_text())[2] == [
            "[[Map/热管理 MOC]]"
        ]
        assert "Inbox/传热" not in other.read_text() and "Resource/传热" in other.read_text()
        es, ms, _ = jobs.snapshot(config, db, cid, vid)
        moved = next(e for e in es if e["path"] == "Resource/传热.md")
        assert moved["id"] == identity
        assert identity in moc_store.load(config, vid, ms[0]["id"]).members
        assert not maintenance.submit(config, db, cid, vid, "quick")["items"]


def test_additive_does_not_move_or_remove_and_repeat_has_zero_changes(config, ready):
    cid, vid, root, _, note = ready
    (root / "Map/数学 MOC.md").write_text("---\ntype: moc\n---\n# 数学 MOC\n")
    note.write_text("---\nmoc: '[[数学 MOC]]'\n---\n# 传热\n#热管理\n傅里叶定律。\n")
    before = note.read_bytes()
    with database.connect(config.state_root) as db:
        first = execute(config, db, preview(config, db, cid, vid, "additive"))
        assert first["state"] == "completed", first
        assert note_moc_fields.field(note.read_text())[2] == ["[[数学 MOC]]", "[[Map/热管理 MOC]]"]
        assert note.read_bytes().endswith(before.split(b"---\n", 2)[2])
        second = preview(config, db, cid, vid, "additive")
        assert all(not i["add"] and not i["remove"] and not i["target"] for i in second["items"])


def test_new_moc_is_created_only_after_confirmation_and_human_topic_kept(config, ready):
    cid, vid, root, _, _ = ready
    with database.connect(config.state_root) as db:
        job = preview(
            config,
            db,
            cid,
            vid,
            "additive",
            new_mocs=[{"title": "材料专题", "topic": "收录半导体封装材料，排除单纯数学推导。"}],
        )
        target = root / job["new_mocs"][0]["path"]
        assert not target.exists()
        result = execute(config, db, job)
        assert result["state"] == "completed", result
        assert "收录半导体封装材料" in target.read_text()


def test_archive_reconcile_after_move_does_not_repeat_membership(config, ready):
    from fiber_bundle import note_placement_execution

    cid, vid, root, _, _ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        original = note_placement_execution.move

        def interrupted(*args):
            original(*args)
            raise OSError("after exclusive rename")

        with patch.object(note_placement_execution, "move", interrupted):
            failed = maintenance.run(config, db, job)
        assert failed["state"] == "partial"
        assert (root / "Resource/传热.md").exists()
        before = len(moc_store.load(config, vid, job["items"][0]["after"][0]).members)
        resumed = maintenance.action(config, db, failed, "resume", None)
        result = maintenance.run(config, db, resumed)
        assert result["state"] == "completed", result
        assert len(moc_store.load(config, vid, job["items"][0]["after"][0]).members) == before


def test_target_conflict_keeps_source_and_can_resume_after_resolution(config, ready):
    cid, vid, root, _, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        job = maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        destination = root / "Resource/传热.md"
        destination.write_text("human file")
        result = maintenance.run(config, db, job)
        assert (
            result["state"] == "partial"
            and note.exists()
            and destination.read_text() == "human file"
        )


def test_external_draft_moves_as_one_package_without_internal_changes(config, ready, package):
    cid, vid, root, _, _ = ready
    import shutil

    target = root / "Inbox" / package.name
    shutil.move(package, target)
    before = {
        p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
    with database.connect(config.state_root) as db:
        registered = packages.register(config, db, cid, vid, "Inbox/" + target.name)
        job = maintenance.submit(config, db, cid, vid, "quick")
        assert any(i["kind"] == "expert" for i in job["items"])
        # A precise user rule is a production-supported classifier, not a model mock.
        policy = jobs.policy(config, vid)
        _, ms, _ = jobs.snapshot(config, db, cid, vid)
        jobs.save_policy(
            config,
            db,
            cid,
            vid,
            {
                **policy["settings"],
                "rules": [
                    {
                        "moc_id": ms[0]["id"],
                        "match": "all",
                        "conditions": [{"field": "kind", "values": ["expert"]}],
                    }
                ],
            },
            policy["revision"],
        )
        job = preview(config, db, cid, vid, "quick")
        result = execute(config, db, job)
        assert result["state"] == "completed", result
        actual = root / "Book" / target.name
        assert not target.exists()
        assert {
            p.relative_to(actual).as_posix(): p.read_bytes()
            for p in actual.rglob("*")
            if p.is_file()
        } == before
        assert store.get(config, "packages", registered["id"])["path"] == "Book/" + target.name
