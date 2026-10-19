"""Offline snapshot, source/permission drift, failure and real expert byte preservation."""

import io
import json
import os
import subprocess
import sys
import zipfile

import pytest

from fiber_bundle import auth, database, published_expert, skill_exports
from fiber_bundle.agent_connections import configuration, knowledge
from fiber_bundle.config import InvalidInput
from fiber_bundle.objects import digest
from fiber_bundle.skill_export_package import download

from .test_access_skill import connect

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_tasks(monkeypatch):
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def setup(config, owner):
    _, _, ids = owner
    root = config.project_root / "test vault/temp_vault1"
    (root / "topic.md").write_text(
        "# Topic\n[Evidence](evidence.md)\n[Missing](absent.md)\n"
        "```markdown\n[Literal example](code.md)\n```\n"
    )
    (root / "code.md").write_text("# Unrelated code example target\nNot a theme member.\n")
    (root / "evidence.md").write_text("# Evidence\n[0–1000 ms] Synthetic source\n")
    with database.connect(config.state_root) as db:
        reader = connect(config, db, ids["personal"], "all")
    return ids["personal"], reader, root


def build(config, db, job):
    for _ in range(len(job["members"]) + 1):
        job = skill_exports.step(config, db, job["id"], job["preview_sha256"])
        assert job["state"] in {"running", "complete"}, job
    assert job["state"] == "complete"
    return job


def test_moc_portable_hashes_history_cancel_and_frozen_sources(config, owner):
    vid, reader, root = setup(config, owner)
    with database.connect(config.state_root) as db:
        items = knowledge.catalog(config, db, reader["client_id"], [vid])["items"]
        moc = next(m for m in items if m["title"] == "Topic")
        request = skill_exports.Preview(
            connection_id=reader["id"],
            vault_id=vid,
            name="research",
            mode="moc",
            module_ids=[moc["module_id"]],
        )
        job = skill_exports.preview(config, db, request)
        assert len(job["members"]) == 2 and job["estimated_content_bytes"] > 0
        assert job["known_gaps"] and all(
            m["completeness_status"] == "unknown" for m in job["members"]
        )
        job = build(config, db, job)
        raw = download(config, db, job["id"])
        assert digest(raw) == job["package_sha256"]
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            prefix = "fiber-theme-research/"
            manifest = json.loads(archive.read(prefix + "manifest.json"))
            for name, sha in manifest["files"].items():
                assert digest(archive.read(prefix + name)) == sha
            topic = archive.read(
                prefix + "modules/" + digest(moc["module_id"].encode())[:24] + "/note.md"
            )
            assert b"../" in topic and b"evidence.md" not in topic
            assert b"```markdown\n[Literal example](code.md)\n```" in topic
            assert str(config.project_root).encode() not in b"".join(
                archive.read(n) for n in archive.namelist()
            )
            assert not any(".local-state" in n or "/../" in n for n in archive.namelist())
        second = skill_exports.preview(config, db, request)
        assert second["id"] != job["id"]
        root.joinpath("evidence.md").write_text("# Evidence\nchanged\n")
        failed = skill_exports.step(config, db, second["id"], second["preview_sha256"])
        assert failed["state"] == "failed" and failed["code"] == "skill_export_source_changed"
        with pytest.raises(InvalidInput):
            download(config, db, job["id"])
        assert (skill_exports.folder(config, job["id"]) / "package.zip").read_bytes() == raw
        row = skill_exports.get(db, second["id"])
        skill_exports.save(db, row, "cancelled")
        with pytest.raises(InvalidInput, match="confirmation"):
            skill_exports.step(config, db, second["id"], second["preview_sha256"])
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


def test_storage_resume_revoke_and_sensitive_content(config, owner, monkeypatch):
    vid, reader, root = setup(config, owner)
    request = skill_exports.Preview(
        connection_id=reader["id"], vault_id=vid, name="test", mode="vault"
    )
    with database.connect(config.state_root) as db:
        job = skill_exports.preview(config, db, request)
        writer = skill_exports.write_private

        def full(*args):
            raise OSError("synthetic no space")

        monkeypatch.setattr(skill_exports, "write_private", full)
        failed = skill_exports.step(config, db, job["id"], job["preview_sha256"])
        assert failed["code"] == "skill_export_storage_unavailable"
        with pytest.raises(InvalidInput, match="not_ready"):
            download(config, db, job["id"])
        monkeypatch.setattr(skill_exports, "write_private", writer)
        complete = build(config, db, failed)
        reader = configuration.control(
            config, db, reader["id"], "pause", reader["revision"], "pause"
        )
        with pytest.raises(auth.Denied):
            download(config, db, complete["id"])
        assert "synthetic no space" not in json.dumps(failed)
        configuration.control(config, db, reader["id"], "enable", reader["revision"], "again")
        (root / "unsafe.md").write_text("# Private\n/Users/synthetic/private-data\n")
        with pytest.raises(InvalidInput, match="sensitive"):
            skill_exports.preview(config, db, request)


def test_http_preview_confirmation_cancel_and_download_guards(config, owner, http):
    vid, reader, _ = setup(config, owner)
    request = dict(connection_id=reader["id"], vault_id=vid, name="http", mode="vault")
    base = "/admin/skill-exports"
    assert (
        http.post(base + "/preview", json=request, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    job = http.post(base + "/preview", json=request).json()
    path = base + "/" + job["id"]
    assert http.get(path + "/download").status_code == 422
    assert (
        http.post(
            path + "/actions", json={"action": "step", "preview_sha256": "f" * 64}
        ).status_code
        == 422
    )
    assert (
        http.post(
            path + "/actions", json={"action": "cancel", "preview_sha256": job["preview_sha256"]}
        ).json()["state"]
        == "cancelled"
    )
    assert http.get(base).json()[0]["state"] == "cancelled"
    http.cookies.clear()
    assert http.get(path).status_code == 401


@pytest.mark.skipif(not os.getenv("FIBER_EXPERT_RUNTIME"), reason="fixed compiler required")
def test_real_expert_offline_child_bytes_and_query(config, owner, policy, monkeypatch, tmp_path):
    from test_expert_authoring import test_real_native_compiler_pi_proposals_and_draft_end_to_end

    test_real_native_compiler_pi_proposals_and_draft_end_to_end(
        config, owner, policy, monkeypatch, False, True, False
    )
    _, _, ids = owner
    with database.connect(config.state_root) as db:
        reader = connect(config, db, ids["personal"], "all")
        task = db.execute(
            "SELECT task_id FROM source_expert_publications WHERE state='published'"
        ).fetchone()[0]
        module = published_expert.load(config, db, reader["client_id"], task)
        job = skill_exports.preview(
            config,
            db,
            skill_exports.Preview(
                connection_id=reader["id"],
                vault_id=ids["personal"],
                name="book",
                module_ids=[module["module_id"]],
            ),
        )
        assert job["members"][0]["completeness_status"] == "not_checked"
        complete = build(config, db, job)
        raw = download(config, db, complete["id"])
        (config.project_root / "test vault/temp_vault1/article.md").write_text(
            "# Article\nAdditional source with unknown completeness.\n"
        )
        calls = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
        mixed = skill_exports.preview(
            config,
            db,
            skill_exports.Preview(
                connection_id=reader["id"], vault_id=ids["personal"], name="mixed", mode="vault"
            ),
        )
        assert {m["kind"] for m in mixed["members"]} == {"note", "expert"}
        build(config, db, mixed)
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == calls
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        archive.extractall(
            tmp_path / "offline"
        )  # Fresh isolated directory, only validated package.
    child = (
        tmp_path / "offline/fiber-theme-book/modules" / digest(module["module_id"].encode())[:24]
    )
    assert {
        p.relative_to(child).as_posix(): p.read_bytes() for p in child.rglob("*") if p.is_file()
    } == module["files"]
    from fiber_bundle.expert_reference import run_package

    result = run_package(child, module["runtime"]["image"], "Synthetic", 5)
    assert result["evidence_ids"] and result["assurance"]["knowledge_verified"] is False
    portable = subprocess.run(
        [
            sys.executable,
            "-S",
            "-E",
            "-B",
            str(child / "scripts/query_reference.py"),
            "--query",
            "Synthetic",
            "--limit",
            "5",
        ],
        cwd=child,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(portable.stdout)["evidence_ids"] == result["evidence_ids"]
