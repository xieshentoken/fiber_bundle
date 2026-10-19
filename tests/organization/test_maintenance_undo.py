from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import database, moc_store, note_identity_store
from fiber_bundle.organization import jobs, maintenance, maintenance_undo, store


def start(config, db, ready, mode="quick", **kwargs):
    cid, vid, root, *_ = ready
    (root / "Inbox/另一份.md").write_text("# 传热补充\n#热管理\n另一份资料。\n")
    job = preview(config, db, cid, vid, mode, **kwargs)
    return maintenance.action(config, db, job, "confirm", job["plan_sha256"])


def test_cancel_reverts_properties_map_path_and_identity_then_new_task_works(config, ready):
    cid, vid, root, moc, note = ready
    before = {p: (p.read_bytes(), p.stat().st_ino) for p in [moc, note]}
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        identity = job["items"][0]["entity"]
        job = maintenance.run(config, db, job)
        assert job["state"] == "running", job
        assert job["completed"] == 1
        maintenance.action(config, db, job, "pause", None)
        maintenance.action(config, db, job, "cancel", None)
        assert job["state"] == "cancelling"
        job = maintenance.run(config, db, store.get(config, "jobs", job["id"]))
        assert job["state"] == "cancelled", job
        assert job["rollback_completed"]
        assert not (root / "Resource/传热.md").exists()
        assert not note_identity_store.records(config, vid)
        assert not store.rows(config, "note_moc", vid)
        assert moc_store.load(config, vid, job["mocs"][0]["id"]) is None
        assert all(
            p.read_bytes() == raw and p.stat().st_ino == ino for p, (raw, ino) in before.items()
        )
        fresh = preview(config, db, cid, vid, "quick")
        assert fresh["id"] != job["id"] and fresh["items"][0]["entity"] == identity
        fresh = maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
        for _ in range(3):
            fresh = maintenance.run(config, db, fresh)
        assert fresh["state"] == "completed", fresh


@pytest.mark.parametrize("target", ["note", "moc"])
def test_cancel_protects_user_edits_and_reports_conflict(config, ready, target):
    cid, vid, root, moc, _ = ready
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        job = maintenance.run(config, db, job)
        changed = root / job["items"][0]["target"] if target == "note" else moc
        changed.write_text(changed.read_text() + "\n用户在 Obsidian 的手动修改。\n")
        human = changed.read_bytes()
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "rollback_conflict", result
        assert changed.read_bytes() == human
        assert "conflict" in result["error"]


def test_undo_resumes_after_interrupted_original_inode_exchange(config, ready):
    cid, vid, root, moc, note = ready
    original = {p: (p.read_bytes(), p.stat().st_ino) for p in [moc, note]}
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        job = maintenance.run(config, db, job)
        maintenance.action(config, db, job, "cancel", None)
        with patch.object(
            maintenance_undo.state, "restore", side_effect=OSError("restart after files")
        ):
            job = maintenance.run(config, db, job)
        assert job["state"] == "rollback_conflict"
        maintenance.action(config, db, job, "resume", None)
        job = maintenance.run(config, db, store.get(config, "jobs", job["id"]))
        assert job["state"] == "cancelled", job
    assert all(
        p.read_bytes() == raw and p.stat().st_ino == ino for p, (raw, ino) in original.items()
    )


def test_cancel_removes_only_new_moc_and_keeps_historical_events(config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = start(
            config,
            db,
            ready,
            mode="additive",
            new_mocs=[{"title": "材料", "topic": "半导体封装材料与制造工艺"}],
        )
        created = root / job["new_mocs"][0]["path"]
        job = maintenance.run(config, db, job)
        assert created.exists() and job["state"] == "running", job
        maintenance.action(config, db, job, "cancel", None)
        job = maintenance.run(config, db, job)
        assert job["state"] == "cancelled", job
        assert not created.exists()
        with moc_store.connection(config) as side:
            assert side.execute("select count(*) from events").fetchone()[0] > 0
        assert not store.rows(config, "decisions", vid)


def test_cancel_after_application_crash_seals_owned_journals_without_new_writes(config, ready):
    cid, vid, root, moc, note = ready
    before = {p: (p.read_bytes(), p.stat().st_ino) for p in [moc, note]}
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        with patch.object(
            maintenance_undo, "seal", side_effect=SystemExit("crash before final snapshot")
        ):
            with pytest.raises(SystemExit):
                maintenance.run(config, db, job)
        current = store.get(config, "jobs", job["id"])
        maintenance.action(config, db, current, "cancel", None)
        current = maintenance.run(config, db, current)
        assert current["state"] == "cancelled", current
    assert all(p.read_bytes() == raw and p.stat().st_ino == ino for p, (raw, ino) in before.items())


def test_cancel_keeps_unrelated_late_manual_decision(config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        job = maintenance.run(config, db, job)
        decision = {"entity": "unrelated", "moc": "unrelated", "state": "pinned"}
        store.put(config, "decisions", "manual-other", vid, decision)
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "cancelled", result
        assert store.get(config, "decisions", "manual-other") == decision


def test_cancel_external_expert_package_restores_whole_directory(config, ready, package):
    import shutil

    from fiber_bundle.organization import packages

    cid, vid, root, moc, _ = ready
    destination = root / "Inbox" / package.name
    shutil.move(package, destination)
    before = {
        p.relative_to(destination).as_posix(): (p.read_bytes(), p.stat().st_ino)
        for p in destination.rglob("*")
        if p.is_file()
    }
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Inbox/" + destination.name)
        policy = jobs.policy(config, vid)
        _, mocs, _ = jobs.snapshot(config, db, cid, vid)
        jobs.save_policy(
            config,
            db,
            cid,
            vid,
            {
                **policy["settings"],
                "rules": [
                    {
                        "moc_id": mocs[0]["id"],
                        "conditions": [{"field": "kind", "values": ["expert"]}],
                    }
                ],
            },
            policy["revision"],
        )
        job = maintenance.submit(config, db, cid, vid, "quick")
        job["items"].sort(key=lambda i: i["kind"] != "expert")
        job = maintenance.run(config, db, job)
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        job = maintenance.run(config, db, job)
        assert job["state"] == "running", job
        assert (root / "Book" / destination.name).exists()
        maintenance.action(config, db, job, "cancel", None)
        for _ in range(5):
            job = maintenance.run(config, db, job)
            if job["state"] != "cancelling":
                break
        assert job["state"] == "cancelled", job
        assert all(
            (destination / p).read_bytes() == data and (destination / p).stat().st_ino == ino
            for p, (data, ino) in before.items()
        )
        assert not (root / "Book" / destination.name).exists()


def test_cancel_managed_intake_note_restores_publication_baseline_and_visibility(config, ready):
    from test_note_moc_locations import managed

    from fiber_bundle import publication_locations

    cid, vid, root, moc, note = ready
    identity, raw, original_plan = managed(config, ready)
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        # Ensure this specific managed note is the first application unit.
        first = next(i for i in job["items"] if i["path"] == "Inbox/传热.md")
        job["items"] = [first] + [i for i in job["items"] if i is not first]
        store.put(config, "jobs", job["id"], vid, job)
        job = maintenance.run(config, db, job)
        assert job["state"] == "running", job
        maintenance.action(config, db, job, "cancel", None)
        job = maintenance.run(config, db, job)
        assert job["state"] == "cancelled", job
        location = publication_locations.get(db, "intake", identity)
        assert location["entry_note_path"] == "Inbox/传热.md"
        assert location["plan"] == original_plan
        assert note.read_bytes() == raw
        es, _, _ = jobs.snapshot(config, db, cid, vid)
        assert any(e["path"] == "Inbox/传热.md" for e in es)


def test_large_cancellation_yields_and_can_pause_resume_without_repeating(config, ready):
    cid, vid, root, moc, note = ready
    before = {moc: moc.read_bytes(), note: note.read_bytes()}
    for n in range(9):
        p = root / f"Inbox/资料{n}.md"
        p.write_text("# 资料\n#热管理\n导热相关正文。\n")
        before[p] = p.read_bytes()
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        for _ in range(5):
            job = maintenance.run(config, db, job)
            assert job["state"] == "running", job
        maintenance.action(config, db, job, "cancel", None)
        job = maintenance.run(config, db, job)
        assert job["state"] == "cancelling" and job["rollback_progress"] > 0, job
        maintenance.action(config, db, job, "pause", None)
        assert job["state"] == "paused"
        maintenance.action(config, db, job, "resume", None)
        for _ in range(5):
            job = maintenance.run(config, db, job)
            if job["state"] != "cancelling":
                break
        assert job["state"] == "cancelled", job
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_cancel_does_not_remove_new_moc_referenced_by_late_user_note(config, ready):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = start(
            config,
            db,
            ready,
            mode="additive",
            new_mocs=[{"title": "材料", "topic": "半导体封装材料与制造工艺"}],
        )
        job = maintenance.run(config, db, job)
        path = job["new_mocs"][0]["path"]
        late = root / "Inbox/用户新建.md"
        late.write_text("# 用户资料\n[[" + path.removesuffix(".md") + "]]\n")
        maintenance.action(config, db, job, "cancel", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "rollback_conflict", result
        assert (root / path).exists() and late.exists()
        assert result["error"] == "maintenance_undo_relationship_conflict"


def test_managed_note_can_be_tracked_again_after_cancel_with_history_retained(config, ready):
    from test_note_moc_locations import managed

    from fiber_bundle import auth, note_identities, publication_file_io

    cid, vid, root, *_ = ready
    managed(config, ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        job = maintenance.run(config, db, job)
        maintenance.action(config, db, job, "cancel", None)
        job = maintenance.run(config, db, job)
        assert job["state"] == "cancelled", job
        client = auth.create_client(db, "after cancel tracking", False)
        auth.set_grant(db, client["id"], vid, query=True, original=True, add=True)
    raw, _ = publication_file_io.read(root, "Inbox/传热.md")
    from fiber_bundle.objects import digest

    result = note_identities.run(
        config, "Bearer " + client["token"], vid, "track", "Inbox/传热.md", digest(raw)
    )
    assert result["revision"] > 1
    with moc_store.connection(config) as side:
        import json

        assert all(
            json.loads(r["record_json"])["revision"] == r["revision"]
            for r in side.execute("SELECT * FROM events")
        )


def test_resumed_legacy_application_cannot_claim_full_cancellation(config, ready):
    cid, vid, root, moc, note = ready
    before = [p.read_bytes() for p in [moc, note]]
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        # Historical applied steps predate this version's before-images.
        job["items"][0]["steps"] = ["link:historical-moc"]
        maintenance_undo.begin(config, db, job, job["items"][0])
        from fiber_bundle.config import IntegrityError

        with pytest.raises(IntegrityError, match="legacy_journal_missing"):
            maintenance.action(config, db, job, "cancel", None)
        assert job["state"] != "cancelled"
    assert [p.read_bytes() for p in [moc, note]] == before
