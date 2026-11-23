from copy import deepcopy
from dataclasses import replace

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_maintenance_undo import start

from fiber_bundle import database, moc_store
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import jobs, maintenance, maintenance_archive, store


def linked_plan(config, db, ready, mode="quick"):
    cid, vid, _, _, _ = ready
    original = jobs.run(config, db, jobs.submit(config, db, cid, vid))
    assert original["state"] == "completed", original
    job = preview(config, db, cid, vid, mode)
    maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    assert job["items"][0]["after"] and not job["items"][0]["add"]
    return job


def remove_membership(config, job):
    mid = job["items"][0]["after"][0]
    record = moc_store.load(config, job["vault_id"], mid)
    moc_store.save(
        config,
        job["vault_id"],
        mid,
        job["client_id"],
        replace(record, revision=record.revision + 1, members={}),
        "synthetic-rollback",
        record.revision,
    )


@pytest.mark.parametrize("mode", ["quick", "additive"])
def test_missing_retained_membership_blocks_resume_before_archive_or_undo(config, ready, mode):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready, mode)
        maintenance.action(config, db, job, "pause", None)
        remove_membership(config, job)
        before = {p: p.read_bytes() for p in root.rglob("*.md")}
        saved = deepcopy(store.get(config, "jobs", job["id"]))
        operations = db.execute("SELECT count(*) FROM vault_operations").fetchone()[0]
        with pytest.raises(IntegrityError, match="maintenance_membership_plan_changed"):
            maintenance.action(config, db, job, "resume", None)
        assert store.get(config, "jobs", job["id"]) == saved
        assert not store.get(config, "maintenance_undo", job["id"])
        assert db.execute("SELECT count(*) FROM vault_operations").fetchone()[0] == operations
        assert all(p.read_bytes() == raw for p, raw in before.items())


def test_retained_relation_does_not_authorize_new_moc_baseline(config, ready):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready)
        maintenance.action(config, db, job, "pause", None)
        mid = job["items"][0]["after"][0]
        record = moc_store.load(config, vid, mid)
        moc_store.save(
            config,
            vid,
            mid,
            cid,
            replace(record, revision=record.revision + 1),
            "synthetic-external-change",
            record.revision,
        )
        expected = deepcopy(job["moc_expected"])
        with pytest.raises(IntegrityError, match="maintenance_membership_baseline_changed"):
            maintenance.action(config, db, job, "resume", None)
        assert job["moc_expected"] == expected
        assert note.exists() and not (root / job["items"][0]["target"]).exists()


def test_progress_checks_live_membership_without_rewriting_history(config, ready):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready, "additive")
        job = maintenance.run(config, db, job)
        assert job["state"] == "completed", job
        saved = deepcopy(store.get(config, "jobs", job["id"]))
        assert (
            maintenance.public(job, config=config, db=db)["items"][0]["membership_observation"][
                "state"
            ]
            == "current"
        )
        remove_membership(config, job)
        result = maintenance.public(job, config=config, db=db)
        assert result["items"][0]["membership_observation"]["state"] == "missing"
        assert result["items"][0]["phase"] == "members_done"
        assert store.get(config, "jobs", job["id"]) == saved
        assert "[[Map/热管理 MOC]]" in note.read_text()  # Owned frontmatter is not manual proof.


def test_manual_frontmatter_is_valid_current_membership(config, ready):
    cid, vid, root, moc, note = ready
    note.write_text("---\nmoc: '[[热管理 MOC]]'\n---\n# 传热\n#热管理\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        assert job["items"][0]["after"] and not job["items"][0]["add"]
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        maintenance.action(config, db, job, "pause", None)
        maintenance.action(config, db, job, "resume", None)
        result = maintenance.run(config, db, job)
        assert result["state"] == "completed", result
        assert (
            maintenance.public(result, config=config, db=db)["items"][0]["membership_observation"][
                "state"
            ]
            == "current"
        )
        assert not moc_store.load(config, vid, job["items"][0]["after"][0])


def test_archive_does_not_adopt_unrelated_map_changes(config, ready):
    cid, vid, root, moc, note = ready
    unrelated = root / "Map/数学 MOC.md"
    unrelated.write_text("---\ntype: moc\n---\n# 数学 MOC\n")
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready)
        other = next(m["id"] for m in job["mocs"] if m["path"] == "Map/数学 MOC.md")
        frozen = deepcopy(job["moc_expected"][other])
        unrelated.write_text(unrelated.read_text() + "\nHuman change\n")
        result = maintenance.run(config, db, job)
        assert result["state"] == "completed", result
        assert job["moc_expected"][other] == frozen
        assert job["items"][0]["phase"] == "archived"
        # Refreshing twice is idempotent, and cannot adopt the unrelated file.
        maintenance_archive.refresh_expected(config, db, job, job["items"][0])
        assert job["moc_expected"][other] == frozen


def test_close_preview_projects_current_archived_memberships(config, ready):
    with database.connect(config.state_root) as db:
        job = start(config, db, ready)
        maintenance.run(config, db, job)
        maintenance.action(config, db, job, "pause", None)
        packet = maintenance.action(config, db, job, "finish_preview", None)
        value = maintenance.public(packet, config=config, db=db)
        assert value["items"][0]["membership_observation"]["state"] == "current"


@pytest.mark.parametrize("mode", ["quick", "additive"])
def test_queued_stale_plan_stops_before_undo_and_returns_actionable_error(config, ready, mode):
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready, mode)
        remove_membership(config, job)
        result = maintenance.run(config, db, job)
        assert result["state"] == "partial"
        assert result["error"] == "maintenance_membership_plan_changed"
        assert result["items"][0]["state"] == "failed"
        assert not store.get(config, "maintenance_undo", job["id"])
        assert ready[4].exists()


def test_resume_http_reports_missing_membership_without_changing_task(http, config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready, "additive")
        maintenance.action(config, db, job, "pause", None)
        remove_membership(config, job)
        saved = deepcopy(store.get(config, "jobs", job["id"]))
    response = http.post(
        f"/admin/organization/maintenance/{job['id']}/resume",
        json={"client_id": cid, "vault_id": vid},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "maintenance_membership_plan_changed"
    assert store.get(config, "jobs", job["id"]) == saved


@pytest.mark.parametrize("unavailable", [False, True])
def test_progress_shares_fresh_map_reads_across_historical_tasks(
    config, ready, monkeypatch, unavailable
):
    from fiber_bundle.organization import overview

    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = linked_plan(config, db, ready, "additive")
        maintenance.run(config, db, job)
        newer = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, newer, "confirm", newer["plan_sha256"])
        maintenance.run(config, db, newer)
        from fiber_bundle.organization import packages

        if unavailable:
            ready[3].unlink()
        seen = []
        original = packages.scoped_read

        def track(root, allowed, path):
            seen.append(path)
            return original(root, allowed, path)

        monkeypatch.setattr(packages, "scoped_read", track)
        result = overview.progress(config, db, cid, vid)
        for task in result["jobs"]:
            if task.get("maintenance"):
                assert task["items"][0]["membership_observation"]["state"] == (
                    "unavailable" if unavailable else "current"
                )
        assert seen == ["Map/热管理 MOC.md"]


def test_manual_completion_can_reference_a_new_registered_map(config, ready):
    from fiber_bundle.organization import manual_membership, overview

    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        job["items"][0]["analysis_error"] = "organization_model_evidence_invalid"
        store.put(config, "jobs", job["id"], vid, job)
        frozen = deepcopy(job)
        (root / "Map/新主题 MOC.md").write_text("---\ntype: moc\n---\n# 新主题 MOC\n")
        _, mocs, _ = jobs.snapshot(config, db, cid, vid)
        mid = next(m["id"] for m in mocs if m["path"] == "Map/新主题 MOC.md")
        result = manual_membership.apply(
            config, db, cid, vid, job["items"][0]["entity"], [mid], "link"
        )
        assert result["completed"] and result["resolved_items"] == 1
        visible = next(
            j for j in overview.progress(config, db, cid, vid)["jobs"] if j["id"] == job["id"]
        )
        assert visible["items"][0]["membership_observation"]["state"] == "current"
        assert visible["items"][0]["membership_observation"]["moc_ids"] == [mid]
        assert store.get(config, "jobs", job["id"]) == frozen


@pytest.mark.parametrize("target_changed", [False, True])
@pytest.mark.parametrize("backlink", [False, True])
def test_manual_frontmatter_archive_reconciles_only_the_proven_moved_file(
    config, ready, monkeypatch, target_changed, backlink
):
    from fiber_bundle import note_placement_execution

    cid, vid, root, moc, note = ready
    note.write_text("---\nmoc: '[[热管理 MOC]]'\n---\n# 传热\n#热管理\n")
    if backlink:
        moc.write_text(moc.read_text() + "\n[[Inbox/传热]]\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert not job["items"][0]["add"] and not job["items"][0]["remove"]
        original = note_placement_execution.move

        def interrupted(*args):
            original(*args)
            raise OSError("synthetic interruption after move")

        with monkeypatch.context() as patch:
            patch.setattr(note_placement_execution, "move", interrupted)
            result = maintenance.run(config, db, job)
        assert result["state"] == "partial", result
        assert result["items"][0]["phase"] == "members_done"
        moved = root / job["items"][0]["target"]
        assert moved.exists() and not note.exists()
        if target_changed:
            moved.write_text(moved.read_text() + "\nHuman change\n")
            with pytest.raises(IntegrityError, match="maintenance_note_changed"):
                maintenance.action(config, db, job, "resume", None)
        else:
            maintenance.action(config, db, job, "resume", None)
            result = maintenance.run(config, db, job)
            assert result["state"] == "completed", result
            assert result["items"][0]["phase"] == "archived"
            if backlink:
                mid = job["items"][0]["after"][0]
                assert job["moc_expected"][mid]["sha256"] == job["mocs"][0]["sha256"]


def test_interrupted_manual_map_exchange_rejects_external_changes(config, ready, monkeypatch):
    from fiber_bundle import note_placement_execution

    cid, vid, root, moc, note = ready
    moc.write_text(moc.read_text() + "\n[[Inbox/传热]]\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        original = note_placement_execution.move

        def interrupted(*args):
            original(*args)
            raise OSError("synthetic interruption after move")

        with monkeypatch.context() as patch:
            patch.setattr(note_placement_execution, "move", interrupted)
            maintenance.run(config, db, job)
        assert job["state"] == "partial" and not note.exists()
        moc.write_text(moc.read_text() + "\nHuman edit after interrupted archive\n")
        before = moc.read_bytes()
        with pytest.raises(IntegrityError, match="maintenance_membership_baseline_changed"):
            maintenance.action(config, db, job, "resume", None)
        assert moc.read_bytes() == before


def test_completed_manual_map_exchange_cannot_rebase_the_next_item(config, ready):
    cid, vid, root, moc, note = ready
    other = root / "Inbox/另一份.md"
    other.write_text("# 第二份传热\n#热管理\n")
    moc.write_text(moc.read_text() + "\n[[Inbox/传热]]\n[[Inbox/另一份]]\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        assert len(job["items"]) == 2
        assert all(not i["add"] and not i["remove"] for i in job["items"])
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        first = maintenance.run(config, db, job)
        assert first["state"] == "running", first
        done = deepcopy(next(i for i in job["items"] if i["state"] == "done"))
        second = maintenance.run(config, db, job)
        assert second["state"] == "partial", second
        assert second["error"] == "maintenance_membership_baseline_changed"
        assert next(i for i in job["items"] if i["state"] == "done") == done
        pending = next(i for i in job["items"] if i["state"] == "failed")
        assert (root / pending["path"]).exists() and not (root / pending["target"]).exists()


def test_malformed_map_does_not_hide_an_unrelated_manual_membership(config, ready):
    cid, vid, root, moc, note = ready
    unrelated = root / "Map/其他 MOC.md"
    unrelated.write_text("---\ntype: moc\n---\n# 其他 MOC\n")
    note.write_text("---\nmoc: '[[热管理 MOC]]'\n---\n# 传热\n#热管理\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "additive")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        result = maintenance.run(config, db, job)
        assert result["state"] == "completed", result
        saved = deepcopy(store.get(config, "jobs", job["id"]))
        unrelated.write_text(unrelated.read_text() + "\n<!-- fiber-moc:invalid begin -->\n")
        visible = maintenance.public(job, config=config, db=db)
        assert visible["items"][0]["membership_observation"]["state"] == "current"
        # The damaged target itself is unverified; failure is isolated to that Map.
        moc.write_text(moc.read_text() + "\n<!-- fiber-moc:invalid begin -->\n")
        visible = maintenance.public(job, config=config, db=db)
        assert visible["items"][0]["membership_observation"]["state"] == "unavailable"
        assert store.get(config, "jobs", job["id"]) == saved
