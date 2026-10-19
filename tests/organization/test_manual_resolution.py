"""Completion receipts leave frozen plans intact while closing historical exceptions."""

import copy

from test_cache_recovery import follow, legacy
from test_jobs import ready as ready

from fiber_bundle import database
from fiber_bundle.organization import (
    jobs,
    maintenance,
    maintenance_plan,
    manual_membership,
    manual_resolution,
    manual_resolution_proof,
    note_moc_sync,
    store,
)


def link(config, parent, entity, mids=None):
    with database.connect(config.state_root) as db:
        _, mocs, _ = jobs.snapshot(config, db, parent["client_id"], parent["vault_id"])
        return manual_membership.apply(
            config,
            db,
            parent["client_id"],
            parent["vault_id"],
            entity,
            mids or [mocs[0]["id"]],
            "link",
        )


def test_success_marks_parent_and_child_without_changing_original_plan_or_cache(
    config, ready, monkeypatch
):
    parent, item, note = legacy(config, ready, monkeypatch)
    parent["error"] = item["analysis_error"]
    store.put(config, "jobs", parent["id"], parent["vault_id"], parent)
    child = follow(config, parent)
    raw = {j["id"]: copy.deepcopy(j) for j in [parent, child]}
    seals = {j["id"]: maintenance_plan.seal(j) for j in [parent, child]}
    calls = store.rows(config, "model_reviews")
    result = link(config, parent, item["entity"])
    assert result["completed"] and result["resolved_items"] == 2
    for identity, original in raw.items():
        stored = store.get(config, "jobs", identity)
        assert stored == original and maintenance_plan.seal(stored) == seals[identity]
        public = maintenance.public(stored, config=config)
        if original.get("error"):
            assert public["error"] is None
            assert public["historical_error"]["error"] == original["error"]
        selected = next(i for i in public["items"] if i["entity"] == item["entity"])
        assert selected["manual_resolution"]["state"] == "resolved"
        assert "moc_evidence" not in selected["manual_resolution"]
        assert selected.get("analysis_error") == next(
            i for i in original["items"] if i["entity"] == item["entity"]
        ).get("analysis_error")
    before = note.read_bytes(), ready[3].read_bytes(), store.rows(config, "manual_resolutions")
    assert link(config, parent, item["entity"])["resolved_items"] == 0
    assert before == (
        note.read_bytes(),
        ready[3].read_bytes(),
        store.rows(config, "manual_resolutions"),
    )
    assert store.rows(config, "model_reviews") == calls


def test_admin_progress_projects_receipt_and_keeps_original_diagnosis(config, ready, http):
    # Keep fixture construction atomic; this HTTP fixture runs a real scheduler.
    with database.locked(config), database.connect(config.state_root) as db:
        parent = maintenance.submit(config, db, ready[0], ready[1], "quick")
        item = parent["items"][0]
        item.update(selected=[], analysis_error="organization_model_evidence_invalid")
        maintenance_plan.difference(item, "quick", parent["fixed"]["save_policy"]["settings"])
        for value in parent["items"]:
            value.update(evaluated=True, state="done")
        parent.update(state="completed", completed=len(parent["items"]))
        store.put(config, "jobs", parent["id"], parent["vault_id"], parent)
    response = http.post(
        "/admin/organization/members",
        json={
            "client_id": parent["client_id"],
            "vault_id": parent["vault_id"],
            "entity_id": item["entity"],
            "moc_id": parent["mocs"][0]["id"],
            "action": "link",
        },
    )
    assert response.status_code == 200 and response.json()["completed"], response.text
    response = http.get(
        "/admin/organization/progress",
        params={"client_id": parent["client_id"], "vault_id": parent["vault_id"]},
    )
    assert response.status_code == 200
    projected = next(j for j in response.json()["jobs"] if j["id"] == parent["id"])
    shown = next(i for i in projected["items"] if i["entity"] == item["entity"])
    assert shown["manual_resolution"]["state"] == "resolved"
    assert shown["analysis_error"] == item["analysis_error"]
    assert store.get(config, "jobs", parent["id"]) == parent


def test_cache_recovery_excludes_resolved_before_changed_hash_and_decision_checks(
    config, ready, monkeypatch
):
    parent, item, _ = legacy(config, ready, monkeypatch)
    assert link(config, parent, item["entity"])["completed"]
    # Its moc write changed the source hash and pinned decision; only image remains.
    child = follow(config, parent)
    assert item["entity"] not in {i["entity"] for i in child["items"]}
    assert [i["title"] for i in child["items"]] == ["图片资料"]


def test_partial_batch_and_unlink_do_not_close_original_error(config, ready, monkeypatch):
    parent, item, _ = legacy(config, ready, monkeypatch)
    (ready[2] / "Map/第二个 MOC.md").write_text("---\ntype: moc\n---\n# 第二个 MOC\n")
    original = note_moc_sync.sync
    monkeypatch.setattr(
        note_moc_sync, "sync", lambda *a, **kw: (_ for _ in ()).throw(OSError("interrupt"))
    )
    result = link(config, parent, item["entity"])
    assert not result["completed"] and not store.rows(config, "manual_resolutions")
    monkeypatch.setattr(note_moc_sync, "sync", original)
    with database.connect(config.state_root) as db:
        _, mocs, _ = jobs.snapshot(config, db, parent["client_id"], parent["vault_id"])
        moc = next(m for m in mocs if m["path"] == "Map/热管理 MOC.md")
        # Removal is a separate manual action and cannot close a classification error.
        result = manual_membership.apply(
            config,
            db,
            parent["client_id"],
            parent["vault_id"],
            item["entity"],
            [moc["id"]],
            "unlink",
        )
    assert result["completed"] and not store.rows(config, "manual_resolutions")


def test_verified_backfill_adds_only_receipts_preserving_files_and_job(config, ready, monkeypatch):
    parent, item, note = legacy(config, ready, monkeypatch)
    # Simulate an older successful mount before completion receipts existed.
    original = manual_resolution.record
    monkeypatch.setattr(manual_resolution, "record", lambda *a, **kw: 0)
    assert link(config, parent, item["entity"])["completed"]
    monkeypatch.setattr(manual_resolution, "record", original)
    before = (
        note.read_bytes(),
        ready[3].read_bytes(),
        copy.deepcopy(store.get(config, "jobs", parent["id"])),
    )
    with database.connect(config.state_root) as db:
        result = manual_resolution_proof.backfill(
            config, db, parent["client_id"], parent["vault_id"]
        )
        again = manual_resolution_proof.backfill(
            config, db, parent["client_id"], parent["vault_id"]
        )
    assert sum(i["items"] for i in result["updated"]) == 1 and not again["updated"]
    assert before == (
        note.read_bytes(),
        ready[3].read_bytes(),
        store.get(config, "jobs", parent["id"]),
    )


def test_different_source_version_and_other_client_do_not_close(config, ready, monkeypatch):
    parent, item, note = legacy(config, ready, monkeypatch)
    other = {**copy.deepcopy(parent), "id": "other-client-job", "client_id": "other"}
    store.put(config, "jobs", other["id"], parent["vault_id"], other)
    note.write_text(note.read_text() + "新的正文版本。\n")
    assert link(config, parent, item["entity"])["completed"]
    assert not manual_resolution.resolved(config, parent)
    assert not manual_resolution.resolved(config, other)


def test_backfill_rejects_pinned_metadata_with_missing_map_projection(config, ready, monkeypatch):
    parent, item, note = legacy(config, ready, monkeypatch)
    assert link(config, parent, item["entity"])["completed"]
    with store.connection(config, write=True) as db:
        db.execute("DELETE FROM manual_resolutions")
    from fiber_bundle import moc_markdown

    with database.connect(config.state_root) as db:
        _, mocs, _ = jobs.snapshot(config, db, parent["client_id"], parent["vault_id"])
        prefix, _, suffix = moc_markdown.split(ready[3].read_bytes(), mocs[0]["id"])
        ready[3].write_bytes(prefix + suffix)
        before = note.read_bytes(), ready[3].read_bytes()
        result = manual_resolution_proof.backfill(
            config, db, parent["client_id"], parent["vault_id"]
        )
    assert not result["updated"] and result["skipped"]
    assert not manual_resolution.resolved(config, parent)
    assert before == (note.read_bytes(), ready[3].read_bytes())


def test_later_manual_moc_is_added_to_receipt_without_duplicate_completion(
    config, ready, monkeypatch
):
    parent, item, _ = legacy(config, ready, monkeypatch)
    assert link(config, parent, item["entity"])["completed"]
    first = manual_resolution.records(config, parent)[item["entity"]]
    (ready[2] / "Map/材料 MOC.md").write_text("---\ntype: moc\n---\n# 材料 MOC\n")
    with database.connect(config.state_root) as db:
        _, mocs, _ = jobs.snapshot(config, db, parent["client_id"], parent["vault_id"])
    new = next(m for m in mocs if m["path"] == "Map/材料 MOC.md")
    assert link(config, parent, item["entity"], [new["id"]])["completed"]
    after = manual_resolution.records(config, parent)[item["entity"]]
    assert set(after["moc_ids"]) == set(first["moc_ids"]) | {new["id"]}
    assert after["resolved_at"] == first["resolved_at"]
    assert link(config, parent, item["entity"], [new["id"]])["resolved_items"] == 0


def test_body_edit_with_already_correct_moc_resolves_only_current_version(
    config, ready, monkeypatch
):
    parent, item, note = legacy(config, ready, monkeypatch)
    assert link(config, parent, item["entity"])["completed"]
    # Keep the prior completion as history, then create a new current-version review.
    note.write_text(note.read_text() + "用户追加新的正文。\n")
    with database.connect(config.state_root) as db:
        newer = maintenance.submit(config, db, parent["client_id"], parent["vault_id"], "deep")
    current = next(i for i in newer["items"] if i["entity"] == item["entity"])
    current.update(
        status="review", analysis_error="organization_model_evidence_invalid", evaluated=True
    )
    newer.update(state="completed")
    store.put(config, "jobs", newer["id"], newer["vault_id"], newer)
    with store.connection(config, write=True) as db:
        db.execute("DELETE FROM manual_resolutions")
    before = note.read_bytes()
    result = link(config, newer, item["entity"])
    assert result["completed"] and result["resolved_items"] == 1
    assert manual_resolution.resolved(config, newer) == {item["entity"]}
    assert not manual_resolution.resolved(config, parent)
    assert note.read_bytes() == before
