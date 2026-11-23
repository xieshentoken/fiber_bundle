"""Quick previews disclose only scoped candidate omissions and preserve legacy plans."""

import json
from pathlib import Path

import pytest
from test_jobs import ready as ready
from test_note_moc_locations import managed

from fiber_bundle import database, note_index, publication_locations
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.organization import jobs, maintenance, maintenance_plan, packages


def pending(config, ready):
    identity, _, plan = managed(config, ready)
    ready[4].write_bytes(b"")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE publication_locations SET state='committing' WHERE publication_id=?",
            (identity,),
        )
    return identity, plan


def test_pending_publication_is_reported_but_ordinary_empty_markdown_is_admitted(config, ready):
    cid, vid, root, moc, hidden = ready
    identity, _ = pending(config, ready)
    empty = root / "Inbox/ordinary-empty.md"
    empty.write_bytes(b"")
    before = {p: p.read_bytes() for p in (moc, hidden, empty)}
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        value = maintenance.public(job)
        report = value["candidate_coverage"]
        assert report["unit"] == "note_or_package" and report["complete"]
        assert report["scope"]["include"] == ["Inbox"]
        assert report["discovered_count"] == 2
        assert report["included_count"] == 1
        assert report["skipped_count"] == 1
        assert report["skipped"] == [
            {
                "path": "Inbox/传热.md",
                "kind": "note",
                "reason": "publication_not_committed",
                "publication_state": "committing",
            }
        ]
        assert not report["skipped_truncated"]
        assert [i["path"] for i in job["items"]] == ["Inbox/ordinary-empty.md"]
        assert "coverage" not in value
        assert publication_locations.get(db, "intake", identity)["state"] == "committing"
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_only_skipped_candidates_return_an_immediate_non_executable_report(config, ready):
    cid, vid, *_ = ready
    pending(config, ready)
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        assert job["state"] == "preview" and job["items"] == []
        assert maintenance.public(job)["candidate_coverage"]["skipped_count"] == 1
        with pytest.raises(InvalidInput, match="maintenance_no_actionable_candidates"):
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        assert not job["confirmed"] and not job["model_called"]


def test_read_failure_is_in_both_candidate_and_existing_safety_coverage(config, ready, monkeypatch):
    cid, vid, root, *_ = ready
    (root / "Inbox/unreadable.md").write_text("locally unavailable")
    reader = note_index.read_note

    def unavailable(base, path, limit, **kwargs):
        if path == "Inbox/unreadable.md":
            raise IntegrityError("cloud_item_not_downloaded")
        return reader(base, path, limit, **kwargs)

    monkeypatch.setattr(note_index, "read_note", unavailable)
    with database.connect(config.state_root) as db:
        value = maintenance.public(maintenance.submit(config, db, cid, vid, "quick"))
    report = value["candidate_coverage"]
    assert report["discovered_count"] == 2 and report["included_count"] == 1
    assert report["skipped"] == [
        {"path": "Inbox/unreadable.md", "kind": "note", "reason": "vault_sync_cloud_pending"}
    ]
    assert value["coverage"]["unavailable_count"] == 1


def test_retained_merge_copies_and_publication_attachment_members_are_not_candidates(config, ready):
    cid, vid, root, *_ = ready
    identity, plan = pending(config, ready)
    retained = root / "Inbox/retained-copy.md"
    retained.write_text("keep retained source")
    attachment = root / "Inbox/private-attachment.md"
    attachment.write_text("private attachment body")
    plan["mapping"]["images/private.md"] = "Inbox/private-attachment.md"
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE publication_locations SET value=? WHERE publication_id=?",
            (canonical(plan), identity),
        )
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,'vault_merge','complete',?,0,0)",
            (
                "retained-test",
                vid,
                canonical(
                    {
                        "plan": {
                            "files": [
                                {"source_vault_id": vid, "source_path": "Inbox/retained-copy.md"}
                            ]
                        }
                    }
                ),
            ),
        )
        report = maintenance.public(maintenance.submit(config, db, cid, vid, "quick"))[
            "candidate_coverage"
        ]
    assert report["discovered_count"] == 1 and report["skipped_count"] == 1
    assert report["skipped"][0]["path"] == "Inbox/传热.md"
    assert "retained-copy" not in json.dumps(report)
    assert "private-attachment" not in json.dumps(report)
    assert retained.read_text() == "keep retained source"
    assert attachment.read_text() == "private attachment body"


def test_only_skipped_candidates_are_delivered_over_the_submission_api(http, config, ready):
    cid, vid, *_ = ready
    pending(config, ready)
    result = http.post(
        "/admin/organization/maintenance",
        json={"client_id": cid, "vault_id": vid, "mode": "quick"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "preview"
    assert result.json()["items"] == []
    assert result.json()["candidate_coverage"]["skipped"][0]["reason"] == (
        "publication_not_committed"
    )


def test_coverage_never_probes_or_discloses_denied_and_non_inbox_paths(config, ready, monkeypatch):
    cid, vid, root, _, _ = ready
    (root / "Inbox/private").mkdir()
    denied = root / "Inbox/private/secret.md"
    denied.write_text("private")
    outside = root / "Resource/outside.md"
    outside.write_text("outside")
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["Inbox/private"]}), vid),
        )
    lstat = Path.lstat
    reader = note_index.read_note

    def guarded_stat(path, *args, **kwargs):
        assert path != denied and root / "Inbox/private" not in path.parents
        return lstat(path, *args, **kwargs)

    def guarded_read(base, path, limit, **kwargs):
        assert path not in {"Inbox/private/secret.md", "Resource/outside.md"}
        return reader(base, path, limit, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded_stat)
    monkeypatch.setattr(note_index, "read_note", guarded_read)
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        report = maintenance.public(job)["candidate_coverage"]
        assert report["discovered_count"] == report["included_count"] == 1
        assert report["skipped"] == []
        assert "secret.md" not in json.dumps(report)
        assert "outside.md" not in json.dumps(report)


def test_registered_package_counts_once_and_never_counts_member_markdown(config, ready, package):
    cid, vid, root, *_ = ready
    destination = root / "Inbox/fake-thermal"
    package.rename(destination)
    with database.connect(config.state_root) as db:
        packages.register(config, db, cid, vid, "Inbox/fake-thermal")
        job = maintenance.submit(config, db, cid, vid, "quick")
        report = maintenance.public(job)["candidate_coverage"]
        assert report["discovered_count"] == report["included_count"] == 2
        assert report["skipped_count"] == 0 and report["skipped"] == []
        assert {i["kind"] for i in job["items"]} == {"note", "expert"}


def test_unregistered_package_is_one_explicit_skipped_candidate(config, ready, package):
    cid, vid, root, *_ = ready
    package.rename(root / "Inbox/fake-thermal")
    with database.connect(config.state_root) as db:
        report = maintenance.submit(config, db, cid, vid, "quick")["candidate_coverage"]
    assert report["discovered_count"] == 2 and report["included_count"] == 1
    assert report["skipped"] == [
        {
            "path": "Inbox/fake-thermal",
            "kind": "package",
            "reason": "expert_package_not_registered",
        }
    ]


def test_truncated_skipped_details_keep_exact_counts(config, ready):
    cid, vid, root, *_ = ready
    for number in range(103):
        (root / f"Inbox/link-{number:03}.md").symlink_to(root / "Inbox/传热.md")
    with database.connect(config.state_root) as db:
        report = maintenance.submit(config, db, cid, vid, "quick")["candidate_coverage"]
    assert report["discovered_count"] == 104
    assert report["skipped_count"] == 103
    assert report["skipped_truncated"] and len(report["skipped"]) == report["skipped_limit"] == 100


def test_two_admitted_notes_archive_to_completion_while_diagnostic_counts_stay_frozen(
    config, ready
):
    cid, vid, root, _, hidden = ready
    pending(config, ready)
    paths = [root / "Inbox/另一篇.md", root / "Inbox/第三篇.md"]
    for path in paths:
        path.write_text("# 传热资料\n\n#热管理\n傅里叶定律与导热。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        report = job["candidate_coverage"].copy()
        assert report["included_count"] == 2 and report["skipped_count"] == 1
        assert "candidate_coverage" not in job["fixed"]
        maintenance.run(config, db, job)
        assert job["state"] == "preview"
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        for _ in range(8):
            maintenance.run(config, db, job)
            if job["state"] not in {"queued", "running"}:
                break
        assert job["state"] == "completed", job
        assert all(item["state"] == "done" for item in job["items"])
        assert maintenance.public(job)["candidate_coverage"] == report
        assert all(not path.exists() and (root / "Resource" / path.name).exists() for path in paths)
        assert hidden.read_bytes() == b""


@pytest.mark.parametrize("change", ["source", "new_visible"])
def test_hidden_publication_state_change_does_not_change_admitted_source_proof(
    config, ready, change
):
    cid, vid, root, *_ = ready
    identity, _ = pending(config, ready)
    (root / "Inbox/visible.md").write_text("# 热管理\n#热管理\n傅里叶定律。\n")
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        maintenance.run(config, db, job)
        seal = job["plan_sha256"]
        db.execute(
            "UPDATE publication_locations SET state='prepared' WHERE publication_id=?",
            (identity,),
        )
        maintenance.validate(config, db, job)
        assert maintenance_plan.seal(job) == seal
        assert job["candidate_coverage"]["skipped"][0]["publication_state"] == "committing"
        path = "Inbox/visible.md" if change == "source" else "Inbox/new-visible.md"
        (root / path).write_text("human changed current source")
        with pytest.raises(IntegrityError, match="maintenance_preview_changed"):
            maintenance.validate(config, db, job)


def test_legacy_preview_counts_stay_unknown_and_its_original_fingerprint_stays_valid(config, ready):
    cid, vid, *_ = ready
    with database.connect(config.state_root) as db:
        job = maintenance.submit(config, db, cid, vid, "quick")
        job.pop("candidate_coverage")
        entities, mocs, _ = jobs.snapshot(config, db, cid, vid)
        context = maintenance_plan.plan_context(config, vid, mocs)
        job["items"] = [
            maintenance_plan.item(config, vid, e, mocs, job["fixed"], "quick", context=context)
            for e in entities
        ]
        maintenance.make_preview(config, db, job, entities, mocs)
        maintenance.validate(config, db, job)
        assert "candidate_coverage" not in maintenance.public(job)
        assert "candidate_coverage" not in job["fixed"]
