"""Synthetic archive readiness and explicit untouched legacy recovery."""

import copy
import json
from unittest.mock import patch

import pytest
from test_jobs import ready as ready
from test_maintenance import preview
from test_note_moc_locations import managed

from fiber_bundle import auth, database, note_placement_execution
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import (
    maintenance,
    maintenance_archive_preview,
    maintenance_plan,
    store,
)


def test_backlink_versions_do_not_require_unrelated_attachment_availability(tmp_path):
    from fiber_bundle import cloud_files, vault_scope

    (tmp_path / "note.md").write_text("# Current source\n")
    (tmp_path / "image.png").write_bytes(b"synthetic image")
    checked = []

    def available(parent, names, **kwargs):
        checked.extend(names)
        if "image.png" in names:
            raise OSError("synthetic attachment not ready")

    with patch.object(cloud_files, "require_available_many", available):
        sources = maintenance_archive_preview.metadata_sources(tmp_path, vault_scope.Scope())
    assert list(sources) == ["note.md"]
    assert "note.md" in checked and "image.png" not in checked


def add_broken_and_independent(ready):
    _, _, root, _, note = ready
    note.write_text(note.read_text() + "\n晶向 [110](上) 和 [111](下)。\n[[missing-reference]]\n")
    later = root / "Inbox/龟尾.md"
    later.write_text("# 传热\n\n#热管理\n独立条目，傅里叶定律。\n")
    return note, later


def legacy(config, db, ready):
    cid, vid, *_ = ready
    add_broken_and_independent(ready)
    managed(config, ready)
    job = preview(config, db, cid, vid, "quick")
    # Recreate the confirmed contract produced before archive readiness existed.
    for item in job["items"]:
        item.pop("archive_preflight", None)
    job.pop("archive_scope", None)
    job["plan_sha256"] = maintenance_plan.seal(job)
    maintenance.action(config, db, job, "confirm", job["plan_sha256"])
    with patch.object(maintenance_archive_preview, "defer", return_value=False):
        maintenance.run(config, db, job)
    assert job["state"] == "partial" and job["error"] == "placement_preview_not_ready", job
    op = operation(db, job)
    value = json.loads(op["payload"])
    for link in value["unresolved_links"]:
        link.pop("line", None)
        link.pop("column", None)
    db.execute("UPDATE vault_operations SET payload=? WHERE id=?", (json.dumps(value), op["id"]))
    return job


def operation(db, job):
    return dict(
        db.execute(
            "SELECT * FROM vault_operations WHERE id=?", (job["items"][0]["archive_operation"],)
        ).fetchone()
    )


def finish_running(config, db, job):
    for _ in range(len(job["items"]) + 3):
        maintenance.run(config, db, job)
        if job["state"] not in {"queued", "running"}:
            return job
    raise AssertionError(job)


@pytest.mark.parametrize("managed_note", [False, True])
def test_preview_separates_mount_and_archive_links_and_independent_items_continue(
    config, ready, managed_note
):
    cid, vid, root, _, _ = ready
    note, later = add_broken_and_independent(ready)
    before = note.read_bytes()
    if managed_note:
        managed(config, ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        broken, independent = job["items"]
        assert broken["add"] and independent["add"]
        assert broken["archive_preflight"]["state"] == "blocked"
        links = broken["archive_preflight"]["links"]
        assert {r["target"] for r in links} == {"上", "下", "missing-reference"}
        assert all(r["line"] > 0 and r["note_path"] == broken["path"] for r in links)
        assert independent["archive_preflight"]["state"] == "ready"
        assert not db.execute(
            "SELECT 1 FROM vault_operations WHERE kind IN ('note_placement','maintenance_archive')"
        ).fetchone()
        assert note.read_bytes() == before
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        finish_running(config, db, job)
        assert job["state"] == "waiting_file", job
        assert broken["state"] == "waiting_archive" and broken["phase"] == "members_done"
        assert not broken.get("archive_operation") and note.read_bytes().endswith(before)
        assert independent["state"] == "done" and independent["phase"] == "archived"
        assert not later.exists() and (root / independent["target"]).exists()
        assert store.get(config, "maintenance_undo", job["id"])["state"] == "ready"
        maintenance.action(config, db, job, "resume", None)
        finish_running(config, db, job)
        assert job["state"] == "waiting_file" and len(broken["steps"]) == 1


def test_preconfirm_edited_source_and_new_inbound_link_require_new_preview(config, ready):
    cid, vid, root, _, note = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        (root / "Resource/new-reference.md").write_text("# 新引用\n[[Inbox/传热]]\n")
        with pytest.raises(IntegrityError, match="maintenance_preview_changed"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert not job["confirmed"] and not store.get(config, "maintenance_undo", job["id"])
        note.write_text(note.read_text() + "\n人工编辑\n")
        with pytest.raises(IntegrityError, match="maintenance_preview_changed"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])


def test_legacy_prepared_can_explicitly_defer_then_finish_without_replaying_original(config, ready):
    cid, vid, root, _, note = ready
    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        original = operation(db, job)
        journal = copy.deepcopy(store.get(config, "maintenance_undo", job["id"]))
        source = note.read_bytes()
        steps = list(job["items"][0]["steps"])
        proof = maintenance.action(config, db, job, "defer_archive_preview", None)[
            "archive_deferral_preview"
        ]
        assert operation(db, job) == original
        assert all(link["line"] > 0 for link in proof["items"][0]["links"])
        assert store.get(config, "maintenance_undo", job["id"]) == journal
        with pytest.raises(InvalidInput, match="deferral_confirmation_required"):
            maintenance.action(config, db, job, "defer_archives", "0" * 64)
        maintenance.action(config, db, job, "defer_archives", proof["plan_sha256"])
        assert operation(db, job) == original and note.read_bytes() == source
        assert store.get(config, "maintenance_undo", job["id"]) == journal
        maintenance.action(config, db, job, "defer_archives", proof["plan_sha256"])
        finish_running(config, db, job)
        assert job["state"] == "waiting_file", job
        assert job["items"][1]["state"] == "done"
        assert job["items"][0]["steps"] == steps
        assert note.read_bytes() == source and not (root / job["items"][0]["target"]).exists()
        assert operation(db, job) == original
        maintenance.action(config, db, job, "resume", None)
        finish_running(config, db, job)
        assert job["state"] == "waiting_file" and operation(db, job) == original
        proof = maintenance.action(config, db, job, "finish_preview", None)["close_preview"]
        journal = copy.deepcopy(store.get(config, "maintenance_undo", job["id"]))
        maintenance.action(config, db, job, "finish", proof["plan_sha256"])
        assert job["state"] == "closed" and operation(db, job) == original
        assert store.get(config, "maintenance_undo", job["id"]) == journal
        assert note.read_bytes() == source


def test_legacy_untouched_prepared_can_close_directly_with_original_journal(config, ready):
    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        op, journal = operation(db, job), store.get(config, "maintenance_undo", job["id"])
        before = {p: (p.read_bytes(), p.stat().st_ino) for p in ready[2].rglob("*.md")}
        with patch.object(
            note_placement_execution, "apply", side_effect=AssertionError("must not replay")
        ):
            proof = maintenance.action(config, db, job, "finish_preview", None)["close_preview"]
            maintenance.action(config, db, job, "finish", proof["plan_sha256"])
        assert job["state"] == "closed" and job["completed"] == 0
        assert (
            operation(db, job) == op and store.get(config, "maintenance_undo", job["id"]) == journal
        )
        assert {p: (p.read_bytes(), p.stat().st_ino) for p in ready[2].rglob("*.md")} == before


@pytest.mark.parametrize(
    "change",
    ["applying", "exchange", "move", "source", "target", "permission", "membership", "journal"],
)
def test_prepared_recovery_refuses_unknown_or_changed_evidence(config, ready, change):
    cid, vid, root, moc, note = ready
    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        op = operation(db, job)
        value = json.loads(op["payload"])
        if change == "applying":
            db.execute("UPDATE vault_operations SET state='applying' WHERE id=?", (op["id"],))
        elif change in {"exchange", "move"}:
            value["exchanges" if change == "exchange" else "move_applied"] = (
                {"fake": {}} if change == "exchange" else True
            )
            db.execute(
                "UPDATE vault_operations SET payload=? WHERE id=?", (json.dumps(value), op["id"])
            )
        elif change == "source":
            note.write_text(note.read_text() + "human edit")
        elif change == "target":
            (root / job["items"][0]["target"]).write_text("human target")
        elif change == "permission":
            auth.set_grant(db, cid, vid, query=True, original=True, add=False)
        elif change == "membership":
            moc.write_text(moc.read_text() + "human map edit")
        else:
            store.put(
                config,
                "maintenance_undo",
                job["id"],
                vid,
                {**store.get(config, "maintenance_undo", job["id"]), "state": "unknown"},
            )
        before = operation(db, job)
        for action in ["defer_archive_preview", "finish_preview"]:
            with pytest.raises((IntegrityError, auth.Denied)):
                maintenance.action(config, db, job, action, None)
        assert operation(db, job) == before and job["state"] == "partial"


def test_worker_preview_includes_readiness_before_sealing(config, ready):
    from fiber_bundle.organization import jobs

    cid, vid, *_ = ready
    add_broken_and_independent(ready)
    with database.connect(config.state_root) as db:
        queued = maintenance.submit(config, db, cid, vid, "quick")
    jobs.tick(config)
    saved = store.get(config, "jobs", queued["id"])
    assert saved["state"] == "preview", saved
    assert saved["items"][0]["archive_preflight"]["state"] == "blocked"
    assert saved["items"][1]["archive_preflight"]["state"] == "ready"
    assert saved["plan_sha256"] == maintenance_plan.seal(saved)


def test_legacy_resume_does_not_requeue_an_immutable_unready_plan(config, ready):
    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        before, journal = copy.deepcopy(job), store.get(config, "maintenance_undo", job["id"])
        op = operation(db, job)
        for _ in range(2):
            with pytest.raises(InvalidInput, match="deferral_review_required"):
                maintenance.action(config, db, job, "resume", None)
            assert job == before and operation(db, job) == op
            assert store.get(config, "maintenance_undo", job["id"]) == journal


@pytest.mark.parametrize("action", ["defer", "close"])
def test_expired_ready_prepared_is_reviewable_without_replaying(config, ready, action):
    cid, vid, root, _, note = ready
    managed(config, ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(
            note_placement_execution, "apply", side_effect=OSError("stopped before apply")
        ):
            maintenance.run(config, db, job)
        op = operation(db, job)
        value = json.loads(op["payload"])
        assert op["state"] == "prepared" and not value["unresolved_links"]
        value["expires_at"] = 1
        db.execute(
            "UPDATE vault_operations SET payload=? WHERE id=?", (json.dumps(value), op["id"])
        )
        op = operation(db, job)
        before = note.read_bytes()
        with pytest.raises(InvalidInput, match="deferral_review_required"):
            maintenance.action(config, db, job, "resume", None)
        assert job["state"] == "partial" and operation(db, job) == op
        if action == "defer":
            proof = maintenance.action(config, db, job, "defer_archive_preview", None)[
                "archive_deferral_preview"
            ]
            assert proof["items"][0]["reason"] == "placement_preview_expired"
            maintenance.action(config, db, job, "defer_archives", proof["plan_sha256"])
            finish_running(config, db, job)
            assert job["state"] == "waiting_file"
        proof = maintenance.action(config, db, job, "finish_preview", None)["close_preview"]
        maintenance.action(config, db, job, "finish", proof["plan_sha256"])
        assert job["state"] == "closed" and operation(db, job) == op
        assert note.read_bytes() == before and not (root / job["items"][0]["target"]).exists()


def test_new_waiting_archive_edited_source_does_not_move_under_old_confirmation(config, ready):
    cid, vid, root, _, note = ready
    add_broken_and_independent(ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        finish_running(config, db, job)
        note.write_text(
            note.read_text().replace("[110](上)", "[110](https://example.com)") + "\n人工修正\n"
        )
        before = note.read_bytes()
        maintenance.action(config, db, job, "resume", None)
        finish_running(config, db, job)
        assert job["state"] == "waiting_file" and job["items"][0]["wait"]["reason"] == "changed"
        assert note.read_bytes() == before and not (root / job["items"][0]["target"]).exists()


def test_deferred_source_is_not_rewritten_by_a_dependent_archive(config, ready):
    cid, vid, root, _, note = ready
    add_broken_and_independent(ready)
    note.write_text(note.read_text() + "\n[[Inbox/龟尾]]\n")
    (root / "Inbox/第三独立篇.md").write_text("# 传热\n#热管理\n真正独立的后续笔记。\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        by_path = {i["path"]: i for i in job["items"]}
        dependent = by_path["Inbox/龟尾.md"]
        assert dependent["archive_preflight"]["reason"] == "maintenance_archive_waiting_dependency"
        assert dependent["archive_preflight"]["blocked_sources"] == ["Inbox/传热.md"]
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        finish_running(config, db, job)
        assert job["state"] == "waiting_file", job
        assert dependent["state"] == "waiting_archive"
        assert "[[Inbox/龟尾]]" in note.read_text()
        assert (root / "Inbox/龟尾.md").exists() and not (root / "Resource/龟尾.md").exists()
        assert by_path["Inbox/第三独立篇.md"]["phase"] == "archived"


def test_legacy_finish_preserves_previously_completed_operations_and_journal(config, ready):
    cid, vid, root, _, note = ready
    add_broken_and_independent(ready)
    (root / "Inbox/A.md").write_text("# 传热\n#热管理\n先完成的独立笔记。\n")
    managed(config, ready)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        for item in job["items"]:
            item.pop("archive_preflight", None)
        job.pop("archive_scope", None)
        job["plan_sha256"] = maintenance_plan.seal(job)
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        with patch.object(maintenance_archive_preview, "defer", return_value=False):
            finish_running(config, db, job)
        assert job["state"] == "partial" and job["completed"] == 1
        before_items = copy.deepcopy(job["items"])
        before_journal = copy.deepcopy(store.get(config, "maintenance_undo", job["id"]))
        operations = [dict(r) for r in db.execute("SELECT * FROM vault_operations ORDER BY id")]
        source = note.read_bytes()
        moved = (root / "Resource/A.md").read_bytes()
        proof = maintenance.action(config, db, job, "finish_preview", None)["close_preview"]
        assert proof["archived"] == 1 and proof["completed"] == 1
        maintenance.action(config, db, job, "finish", proof["plan_sha256"])
        assert job["items"] == before_items and job["completed"] == 1
        assert store.get(config, "maintenance_undo", job["id"]) == before_journal
        assert [
            dict(r) for r in db.execute("SELECT * FROM vault_operations ORDER BY id")
        ] == operations
        assert note.read_bytes() == source and (root / "Resource/A.md").read_bytes() == moved


def test_malformed_old_source_files_cannot_read_an_unbound_path(config, ready):
    from fiber_bundle import publication_file_io

    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        op = operation(db, job)
        value = json.loads(op["payload"])
        value["source_files"]["Restricted/private.md"] = next(iter(value["source_files"].values()))
        db.execute(
            "UPDATE vault_operations SET payload=? WHERE id=?", (json.dumps(value), op["id"])
        )
        read = publication_file_io.read
        observed = []

        def track(root, path):
            observed.append(path)
            assert path != "Restricted/private.md"
            return read(root, path)

        with patch.object(publication_file_io, "read", side_effect=track):
            with pytest.raises(IntegrityError, match="maintenance_archive_binding_changed"):
                maintenance.action(config, db, job, "defer_archive_preview", None)
        assert "Restricted/private.md" not in observed


def test_unrelated_external_edit_is_checked_once_at_confirmation_without_preview_body_reads(
    config, ready, monkeypatch
):
    from fiber_bundle import note_index

    cid, vid, root, *_ = ready
    outside = root / "Resource/unrelated.md"
    outside.write_text("# unrelated\nUnrelated material.\n")
    seen = []
    original = note_index.read_note

    def track(root, path, limit, **kwargs):
        seen.append(path)
        return original(root, path, limit, **kwargs)

    monkeypatch.setattr(note_index, "read_note", track)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        assert "Resource/unrelated.md" not in seen
        outside.write_text(outside.read_text() + "\nUnrelated correction.\n")
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert job["confirmed"] and job["state"] == "queued"
        assert seen.count("Resource/unrelated.md") == 1


def test_changed_external_backlink_marks_preview_stale_and_new_request_does_not_reuse_it(
    config, ready
):
    cid, vid, root, *_ = ready
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        outside = root / "Resource/reference.md"
        outside.write_text("# reference\n[[Inbox/传热]]\n")
        public = maintenance.public(job, config=config, db=db)
        assert public["preview_validity"]["state"] == "invalidated"
        assert public["preview_validity"]["issues"] == [
            {"path": "Resource/reference.md", "reason": "backlink_changed"}
        ]
        fresh = maintenance.submit(config, db, cid, vid, "quick")
        assert fresh["id"] != job["id"] and not fresh["confirmed"]
        maintenance.run(config, db, fresh)
        maintenance.action(config, db, fresh, "confirm", fresh["plan_sha256"])
        finish_running(config, db, fresh)
        assert fresh["state"] == "completed", fresh
        assert "Resource/传热" in outside.read_text()


@pytest.mark.parametrize("change", ["missing", "budget"])
def test_unavailable_external_dependency_lists_paths_without_confirming_or_reading_everything(
    config, ready, change
):
    cid, vid, root, *_ = ready
    paths = [root / f"Resource/external-{i}.md" for i in range(33 if change == "budget" else 1)]
    for path in paths:
        path.write_text("# unrelated\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        if change == "missing":
            paths[0].unlink()
        else:
            for path in paths:
                path.write_text("# unrelated edited\n")
        with pytest.raises(
            IntegrityError, match="maintenance_archive_dependency_unavailable"
        ) as failed:
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert failed.value.issues
        assert failed.value.issues[0]["path"].startswith("Resource/external-")
        assert failed.value.issues[0]["reason"] == (
            "source_missing" if change == "missing" else "source_budget"
        )
        assert not job["confirmed"] and job["state"] == "preview"
        assert not store.get(config, "maintenance_undo", job["id"])


@pytest.fixture
def stored_legacy_job(config, ready):
    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        return job, operation(db, job)


def test_http_legacy_resume_and_confirmation_return_actionable_codes_without_requeue(
    stored_legacy_job, http, config, ready
):
    cid, vid, *_ = ready
    job, op = stored_legacy_job
    url = "/admin/organization/maintenance/" + job["id"]
    scope = {"client_id": cid, "vault_id": vid}
    failed = http.post(url + "/resume", json=scope)
    assert failed.status_code == 409, failed.text
    assert failed.json()["error"] == "maintenance_archive_deferral_review_required"
    assert store.get(config, "jobs", job["id"])["state"] == "partial"
    preview_response = http.post(url + "/defer_archive_preview", json=scope)
    assert preview_response.status_code == 200, preview_response.text
    assert all(
        link["line"] > 0
        for link in preview_response.json()["archive_deferral_preview"]["items"][0]["links"]
    )
    stale = http.post(url + "/defer_archives", json={**scope, "plan_sha256": "0" * 64})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"] == "maintenance_archive_deferral_confirmation_required"
    token = preview_response.json()["archive_deferral_preview"]["plan_sha256"]
    with database.locked(config):
        current = store.get(config, "jobs", job["id"])
        current.update(
            index_state="ready",
            index_error=None,
            index_request_id="new-index",
            index_requested_at=100,
        )
        store.put(config, "jobs", current["id"], vid, current)
    deferred = http.post(url + "/defer_archives", json={**scope, "plan_sha256": token})
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["items"][0]["archive_deferral"]["operation"] == op["id"]
    with database.connect(config.state_root) as db:
        assert operation(db, job) == op


def test_deferral_and_finish_tokens_ignore_index_progress_but_bind_execution_generation(
    config, ready
):
    with database.connect(config.state_root) as db:
        job = legacy(config, db, ready)
        proof = maintenance.action(config, db, job, "defer_archive_preview", None)[
            "archive_deferral_preview"
        ]
        close = maintenance.action(config, db, job, "finish_preview", None)["close_preview"]
        job.update(
            index_state="ready",
            index_error=None,
            index_request_id="new",
            index_requested_at=123,
            updated_at=456,
            activity={"phase": "index"},
            timings={"index": {"seconds": 12}},
            read_work={"body_reads": 100},
        )
        assert (
            maintenance.action(config, db, job, "defer_archive_preview", None)[
                "archive_deferral_preview"
            ]["plan_sha256"]
            == proof["plan_sha256"]
        )
        assert (
            maintenance.action(config, db, job, "finish_preview", None)["close_preview"][
                "plan_sha256"
            ]
            == close["plan_sha256"]
        )
        job["generation"] = job.get("generation", 0) + 1
        with pytest.raises(InvalidInput, match="deferral_confirmation_required"):
            maintenance.action(config, db, job, "defer_archives", proof["plan_sha256"])
        with pytest.raises(InvalidInput, match="close_confirmation_required"):
            maintenance.action(config, db, job, "finish", close["plan_sha256"])


@pytest.mark.parametrize("count", [1, 2])
def test_old_preview_confirmation_retries_census_without_resealing_or_writes(config, ready, count):
    from test_link_census_regression import census_change, touch_directory

    from fiber_bundle import note_read_session

    cid, vid, root, moc, note = ready
    if count == 2:
        (root / "Inbox/Second.md").write_text("# 热管理\n\n#热管理\n傅里叶定律。\n")
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        sealed = job["plan_sha256"]
        binding = copy.deepcopy(job["items"][0]["archive_preflight"])
        original = {p: (p.read_bytes(), p.stat().st_ino) for p in root.rglob("*.md")}
        with (
            note_read_session.reuse() as session,
            census_change(touch_directory, finalizing=False) as scans,
        ):
            maintenance.action(config, db, job, "confirm", sealed)
        assert len(scans) == 2 and session.counts["cloud_rechecks"] == 1
        assert job["confirmed"] and job["confirmed_sha256"] == sealed
        assert job["items"][0]["archive_preflight"] == binding
        assert all((p.read_bytes(), p.stat().st_ino) == before for p, before in original.items())
        finish_running(config, db, job)
        assert job["state"] == "completed" and job["items"][0]["phase"] == "archived"
        assert all(item["phase"] == "archived" for item in job["items"])
        assert not note.exists() and (root / job["items"][0]["target"]).is_file()


@pytest.mark.parametrize(
    "change",
    [
        "new_note", "new_attachment", "body", "replacement", "directory", "permission",
        "grant", "continuous",
    ],
)
def test_confirmation_census_retry_keeps_original_preview_and_refuses_real_changes(
    config, ready, change
):
    import os
    import stat

    from test_link_census_regression import census_change, touch_directory

    from fiber_bundle import note_read_session

    cid, vid, root, moc, note = ready
    extra = root / "Extra"
    extra.mkdir()
    mode = stat.S_IMODE(root.stat().st_mode)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        sealed = job["plan_sha256"]
        binding = copy.deepcopy(job["items"][0]["archive_preflight"])
        before = note.read_bytes(), note.stat().st_ino, moc.read_bytes(), moc.stat().st_ino
        operations = [
            tuple(row) for row in db.execute("SELECT * FROM vault_operations ORDER BY id")
        ]

        def mutate(directory):
            if change == "new_note":
                (directory / "Fresh.md").write_text("# Fresh\n[[Inbox/传热]]\n")
            elif change == "new_attachment":
                (directory / "Fresh.png").write_bytes(b"synthetic attachment")
            elif change == "body":
                note.write_text(note.read_text() + "\nHuman edit\n")
            elif change == "replacement":
                replacement = directory / ".replacement.md"
                replacement.write_bytes(note.read_bytes())
                replacement.replace(note)
            elif change == "directory":
                extra.rename(directory / ".original-extra")
                extra.mkdir()
            elif change == "permission":
                directory.chmod(mode & ~stat.S_IRUSR)
            elif change == "grant":
                auth.set_grant(db, cid, vid, query=True, original=True, add=False)
            touch_directory(directory)

        try:
            with (
                note_read_session.reuse() as session,
                census_change(mutate, finalizing=False, repeatedly=change == "continuous") as scans,
                pytest.raises((IntegrityError, auth.Denied)),
            ):
                maintenance.action(config, db, job, "confirm", sealed)
        finally:
            os.chmod(root, mode)
        assert len(scans) == (1 if change == "permission" else 2)
        assert session.counts.get("cloud_rechecks", 0) == (change != "permission")
        assert job["state"] == "preview" and not job["confirmed"]
        assert job["plan_sha256"] == sealed and job["items"][0]["archive_preflight"] == binding
        assert not store.get(config, "maintenance_undo", job["id"])
        assert operations == [
            tuple(row) for row in db.execute("SELECT * FROM vault_operations ORDER BY id")
        ]
        assert (moc.read_bytes(), moc.stat().st_ino) == before[2:]
        if change not in {"body", "replacement"}:
            assert (note.read_bytes(), note.stat().st_ino) == before[:2]
        elif change == "body":
            assert note.read_bytes() == before[0] + b"\nHuman edit\n"
        else:
            assert note.read_bytes() == before[0] and note.stat().st_ino != before[1]
