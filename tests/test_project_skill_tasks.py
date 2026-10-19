"""Frozen import/Pi bindings stay on their own Skill across updates or disabling."""

import json

import pytest
from document_support import pdf_bytes
from test_project_skill_files import project_skill as project_skill
from test_source_imports import owner as owner
from test_source_imports import request, upload

from fiber_bundle import (
    database,
    expert_runtime,
    pi_processing,
    project_skill_files,
    project_skill_runtime,
    project_skills,
    source_processing_policy,
)
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_source_imports", "test_execution"]
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def enabled_skill(config, project_skill, monkeypatch):
    def synthetic_qualification(config, snapshot, image):
        return expert_runtime.receipt(image, snapshot["compiler_sha256"], {"passed": True})

    monkeypatch.setattr(project_skill_runtime, "qualify", synthetic_qualification)
    project_skills.save(config, True, IMAGE, None)
    return project_skill


def test_new_import_uses_updated_skill_old_submission_and_pi_keep_snapshot(
    config, owner, policy, enabled_skill
):
    cid, _, ids = owner
    raw = pdf_bytes(("1 Technical material\nDetailed source evidence for a native reference.",))
    with database.connect(config.state_root) as db:
        req = request(cid, ids["personal"], raw, "book.pdf", "process", None)
        req.update(engine="fake-expert", expert_profile="native-text")
        first = upload(config, db, req, raw)
        old = first["plan"]["project_skill"]
        rid = pi_processing.prepare(
            config,
            db,
            policy,
            cid,
            ids["personal"],
            db.execute(
                "SELECT source_version_id FROM receipts WHERE job_id=?", (first["receipt_id"],)
            ).fetchone()[0],
            "expert",
            first["source_sha256"],
            [{"id": "span", "text": "source evidence"}],
            allow_cloud=True,
            project_skill=old,
        )
        (enabled_skill / "SKILL.md").write_text(
            (enabled_skill / "SKILL.md").read_text() + "\nVersion B\n"
        )
        # Repeating the exact request remains the same old import before any new selection.
        from fiber_bundle import source_imports

        assert source_imports.create(config, db, req)["id"] == first["id"]
        req["submission_id"] += "-new"
        second = upload(config, db, req, raw)
        new = second["plan"]["project_skill"]
        assert new["snapshot_sha256"] != old["snapshot_sha256"]
        assert new["pi_sha256"] != old["pi_sha256"]
        binding = pi_processing.get(db, rid)
        pi_processing.materialize(config, binding)
        assert binding["skill_sha256"] == old["pi_sha256"]
        mounts = project_skill_runtime.pi_mounts(
            config, json.loads(binding["input_json"])["project_skill"]
        )
        assert any(old["snapshot_sha256"] in part for part in mounts)
        assert not any(new["snapshot_sha256"] in part for part in mounts)
        current, revision = project_skills.settings(config)
        project_skills.save(config, False, current["image"], revision)
        pi_processing.materialize(config, binding)
        assert project_skills.resolve(config) is None
        assert project_skill_files.verify(config, old["snapshot_sha256"])
        assert source_processing_policy.inspect(config, None)["value"] is None
        assert db.execute("SELECT count(*) FROM source_imports").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_enabled_broken_source_does_not_fallback_or_create_new_import(config, owner, enabled_skill):
    cid, _, ids = owner
    (enabled_skill / "SKILL.md").unlink()
    with database.connect(config.state_root) as db:
        req = request(cid, ids["personal"], b"pdf", "book.pdf", "process", None)
        req.update(engine="fake-expert", expert_profile="native-text")
        from fiber_bundle import source_imports

        with pytest.raises(InvalidInput):
            source_imports.create(config, db, req)
        assert db.execute("SELECT count(*) FROM source_imports").fetchone()[0] == 0


def test_project_skill_http_auth_cas_and_no_worker_policy_mutation(
    config, project_skill, http, monkeypatch
):
    monkeypatch.setattr(
        project_skill_runtime,
        "qualify",
        lambda c, s, i: expert_runtime.receipt(i, s["compiler_sha256"], {"passed": True}),
    )
    before = http.get("/admin/source-policy").json()["configuration_sha256"]
    assert http.get("/admin/project-skills").json()["state"] == "detected"
    payload = {"enabled": True, "image": IMAGE, "expected_sha256": None}
    response = http.put("/admin/project-skills", json=payload)
    assert response.status_code == 200, response.json()
    assert response.json()["settings"]["enabled"] is True
    assert http.put("/admin/project-skills", json=payload).status_code == 409
    assert http.get("/admin/source-policy").json()["configuration_sha256"] == before
    assert not (config.state_root / "configs/source-processing.json").exists()
    http.headers.pop("X-CSRF-Token")
    assert http.put("/admin/project-skills", json=payload).status_code == 403


@pytest.mark.parametrize("raw", ["not json", "[]", "null"])
def test_invalid_project_settings_have_stable_error(config, raw):
    path = config.state_root / "configs/project-skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw)
    with pytest.raises(InvalidInput, match="project_skill_settings_invalid"):
        project_skills.settings(config)


def test_unavailable_docker_reports_runtime_error(config, project_skill, monkeypatch):
    from fiber_bundle import executor_container

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("synthetic Docker unavailable")

    monkeypatch.setattr(project_skill_runtime.subprocess, "run", unavailable)
    monkeypatch.setattr(executor_container, "stop", unavailable)
    with pytest.raises(InvalidInput, match="project_skill_runtime_unavailable"):
        project_skills.save(config, True, IMAGE, None)
    assert project_skills.settings(config)[0]["enabled"] is False
