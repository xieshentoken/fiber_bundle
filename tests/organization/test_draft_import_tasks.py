import hashlib
from uuid import uuid4

import pytest
from test_draft_import_preview import draft as draft

from fiber_bundle import auth, database
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.organization import draft_import_tasks as tasks
from fiber_bundle.organization import store
from fiber_bundle.organization.draft_import_runtime import Runtime


def start(config, draft):
    cid, vid, _, source = draft
    runtime = Runtime(config)
    with database.locked(config), database.connect(config.state_root) as db:
        value = tasks.create(
            config,
            db,
            {
                "submission_id": str(uuid4()),
                "client_id": cid,
                "vault_id": vid,
                "source_path": str(source),
                "folder": "",
            },
        )
    runtime.run(value["id"])
    result = store.get(config, tasks.TABLE, value["id"])
    assert result["state"] == "preview", result
    return runtime, result


def confirm(config, runtime, task, moc_ids=()):
    with database.locked(config), database.connect(config.state_root) as db:
        tasks.confirm(
            config,
            db,
            task,
            {
                "preview_sha256": task["preview_sha256"],
                "moc_ids": list(moc_ids),
                "candidate_acknowledged": True,
            },
        )
    runtime.run(task["id"])
    return store.get(config, tasks.TABLE, task["id"])


def hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_import_keeps_source_and_package_bytes_and_duplicate_identity(config, draft):
    before = hashes(draft[3])
    runtime, t = start(config, draft)
    result = confirm(config, runtime, t)
    assert result["state"] == "completed", (result["error"], result["phase"], result["moc_results"])
    assert result["index_state"] == "ready"
    assert hashes(draft[3]) == before == hashes(draft[2] / result["relative_path"])
    runtime, t = start(config, draft)
    assert t["reuse"]
    again = confirm(config, runtime, t)
    assert again["state"] == "completed", again
    assert len(store.rows(config, "packages", draft[1])) == 1
    assert again["copied_bytes"] == 0


def test_manual_multi_moc_preserves_human_content_and_package(config, draft):
    for title in ("热管理", "仿真"):
        (draft[2] / ("Map/" + title + " MOC.md")).write_text(
            "---\ntype: moc\n---\n# " + title + "\n人工内容\n"
        )
    original = hashes(draft[3])
    runtime, t = start(config, draft)
    mids = [m["id"] for m in t["mocs"]]
    assert len(mids) == 2
    result = confirm(config, runtime, t, mids)
    assert result["state"] == "completed", (result["error"], result["phase"], result["moc_results"])
    assert len(result["moc_results"]) == 2
    assert all(r["completed"] for r in result["moc_results"])
    assert all("人工内容" in p.read_text() for p in (draft[2] / "Map").glob("*.md"))
    assert original == hashes(draft[2] / result["relative_path"])
    assert (
        len([d for d in store.rows(config, "decisions", draft[1]) if d["state"] == "pinned"]) == 2
    )


def test_cancel_then_resume_uses_frozen_intent(config, draft):
    runtime, t = start(config, draft)
    with database.locked(config), database.connect(config.state_root) as db:
        tasks.confirm(
            config,
            db,
            t,
            {"preview_sha256": t["preview_sha256"], "moc_ids": [], "candidate_acknowledged": True},
        )
        tasks.action(config, t, "cancel")
    assert not (draft[2] / t["relative_path"]).exists()
    with database.locked(config):
        tasks.action(config, t, "resume")
    runtime.run(t["id"])
    result = store.get(config, tasks.TABLE, t["id"])
    assert result["state"] == "completed", (result["error"], result["phase"], result["moc_results"])


def test_modified_source_stops_without_publishing(config, draft):
    runtime, t = start(config, draft)
    (draft[3] / "SKILL.md").write_text("changed")
    result = confirm(config, runtime, t)
    assert result["state"] == "failed" and result["error"] == "draft_source_changed"
    assert not (draft[2] / t["relative_path"]).exists()


def test_destination_created_after_preview_not_overwritten(config, draft):
    runtime, t = start(config, draft)
    dest = draft[2] / t["relative_path"]
    dest.mkdir()
    (dest / "human").write_text("keep")
    result = confirm(config, runtime, t)
    assert result["state"] == "failed"
    assert (dest / "human").read_text() == "keep"
    assert not store.rows(config, "packages", draft[1])


def test_index_failure_resume_does_not_copy_or_link_again(config, draft, monkeypatch):
    from fiber_bundle.organization import draft_import_runtime as worker

    runtime, t = start(config, draft)
    original = worker.note_search_build.build
    monkeypatch.setattr(
        worker.note_search_build,
        "build",
        lambda *a: (_ for _ in ()).throw(InvalidInput("index_problem")),
    )
    result = confirm(config, runtime, t)
    assert result["state"] == "failed" and result["phase"] == "index", result
    before = hashes(draft[2] / result["relative_path"])
    monkeypatch.setattr(worker.note_search_build, "build", original)
    monkeypatch.setattr(
        worker.Runtime, "register_and_link", lambda *a: pytest.fail("registered twice")
    )
    with database.locked(config):
        tasks.action(config, result, "resume")
    runtime.run(t["id"])
    done = store.get(config, tasks.TABLE, t["id"])
    assert done["state"] == "completed", done
    assert before == hashes(draft[2] / result["relative_path"])


def test_foreign_identity_and_revocation_rejected(config, draft):
    runtime, t = start(config, draft)
    with database.locked(config), database.connect(config.state_root) as db:
        stranger = auth.create_client(db, "other", False)
        auth.set_grant(db, stranger["id"], draft[1], query=True, add=True, original=True)
        with pytest.raises(auth.Denied):
            tasks.owned(config, db, t["id"], stranger["id"], draft[1])
        auth.set_grant(db, draft[0], draft[1], query=True, add=False, original=True)
        with pytest.raises(auth.Denied):
            tasks.confirm(
                config,
                db,
                t,
                {
                    "preview_sha256": t["preview_sha256"],
                    "moc_ids": [],
                    "candidate_acknowledged": True,
                },
            )


def test_cancel_during_copy_resume_skips_complete_members(config, draft, monkeypatch):
    from fiber_bundle.organization import draft_import_files

    runtime, t = start(config, draft)
    original = draft_import_files.copy_member
    calls = []

    def interrupt(root, name, *args):
        original(root, name, *args)
        calls.append(name)
        if len(calls) == 1:
            with database.locked(config):
                latest = store.get(config, tasks.TABLE, t["id"])
                tasks.action(config, latest, "cancel")

    monkeypatch.setattr(draft_import_files, "copy_member", interrupt)
    result = confirm(config, runtime, t)
    assert result["state"] == "cancelled", result
    assert not (draft[2] / t["relative_path"]).exists()
    monkeypatch.setattr(draft_import_files, "copy_member", original)
    with database.locked(config):
        tasks.action(config, result, "resume")
    runtime.run(t["id"])
    done = store.get(config, tasks.TABLE, t["id"])
    assert done["state"] == "completed", done
    assert hashes(draft[3]) == hashes(draft[2] / t["relative_path"])


def test_crash_after_directory_rename_adopts_recorded_identity(config, draft, monkeypatch):
    from fiber_bundle import file_exchange

    runtime, t = start(config, draft)
    original = file_exchange.rename

    def crash(source_fd, source, target_fd, target, **kw):
        original(source_fd, source, target_fd, target, **kw)
        if source == "payload":
            raise OSError("synthetic_crash")

    monkeypatch.setattr(file_exchange, "rename", crash)
    result = confirm(config, runtime, t)
    assert result["state"] == "failed" and (draft[2] / t["relative_path"]).exists()
    monkeypatch.setattr(file_exchange, "rename", original)
    with database.locked(config):
        tasks.action(config, result, "resume")
    runtime.run(t["id"])
    done = store.get(config, tasks.TABLE, t["id"])
    assert done["state"] == "completed", done
    assert len(store.rows(config, "packages", draft[1])) == 1


def test_partial_moc_recovery_only_finishes_remaining(config, draft, monkeypatch):
    from fiber_bundle.organization import draft_import_runtime as worker

    for title in ("一", "二"):
        (draft[2] / ("Map/" + title + " MOC.md")).write_text(
            "---\ntype: moc\n---\n# " + title + "\n"
        )
    runtime, t = start(config, draft)
    mids = [m["id"] for m in t["mocs"]]
    original = worker.manual_membership.apply
    calls = []

    def partial(config, db, cid, vid, entity, selected, action):
        calls.append(selected)
        result = original(config, db, cid, vid, entity, selected[:1], action)
        result["completed"] = False
        result["results"].append(
            {"moc_id": selected[1], "title": "二", "completed": False, "error": "synthetic"}
        )
        return result

    monkeypatch.setattr(worker.manual_membership, "apply", partial)
    result = confirm(config, runtime, t, mids)
    assert result["state"] == "partial", result
    assert tasks.pending(config, draft[1]) == {t["package"]["module_id"]}
    monkeypatch.setattr(worker.manual_membership, "apply", original)
    with database.locked(config):
        tasks.action(config, result, "resume")
    runtime.run(t["id"])
    done = store.get(config, tasks.TABLE, t["id"])
    assert done["state"] == "completed", done
    assert tasks.pending(config, draft[1]) == set()
    assert len(done["moc_results"]) == 2 and all(r["completed"] for r in done["moc_results"])


def test_runtime_restart_requires_resume_of_confirmed_task(config, draft):
    runtime, t = start(config, draft)
    with database.locked(config), database.connect(config.state_root) as db:
        tasks.confirm(
            config,
            db,
            t,
            {"preview_sha256": t["preview_sha256"], "moc_ids": [], "candidate_acknowledged": True},
        )
    new = Runtime(config)
    new.start()
    new.close()
    saved = store.get(config, tasks.TABLE, t["id"])
    assert saved["state"] == "interrupted"
    assert not (draft[2] / t["relative_path"]).exists()


def test_repeated_confirmation_is_idempotent(config, draft):
    runtime, t = start(config, draft)
    done = confirm(config, runtime, t)
    with database.locked(config), database.connect(config.state_root) as db:
        assert (
            tasks.confirm(
                config,
                db,
                done,
                {
                    "preview_sha256": t["preview_sha256"],
                    "moc_ids": [],
                    "candidate_acknowledged": True,
                },
            )["state"]
            == "completed"
        )
        with pytest.raises(IntegrityError, match="confirmation_conflict"):
            tasks.confirm(
                config,
                db,
                done,
                {
                    "preview_sha256": t["preview_sha256"],
                    "moc_ids": [str(uuid4())],
                    "candidate_acknowledged": True,
                },
            )
