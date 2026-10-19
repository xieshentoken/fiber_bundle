"""Real offline compiler and Pi containers; only the upstream model response is synthetic."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from document_support import pdf_bytes
from test_expert_authoring import proposal
from test_source_imports import owner as owner
from test_source_imports import request, upload

from fiber_bundle import (
    automatic_ingestion,
    automatic_policy,
    database,
    expert_authoring,
    expert_authoring_runner,
    expert_tasks,
    model_budgets,
    model_gateway,
    pi_processing,
    project_skill_files,
    project_skills,
)
from fiber_bundle.inputs import canonical

pytest_plugins = ["test_source_imports", "test_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("FIBER_PROJECT_SKILL_SOURCE"),
    reason="explicit local compiler Skill and Docker required",
)


def install(config):
    source = Path(os.environ["FIBER_PROJECT_SKILL_SOURCE"])
    target = config.project_root / "skills/fake-expert"
    for name, raw in project_skill_files.read_tree(source).items():
        p = target / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    marker = "FIBER_PROJECT_SKILL_MARKER_A"
    p = target / "SKILL.md"
    p.write_text(p.read_text() + "\n" + marker + "\n")
    image = json.loads(Path(os.environ["FIBER_EXPERT_RUNTIME"]).read_text())["image"]
    project_skills.save(config, True, image, None)
    return target, marker


def setup_book(config, owner, policy, monkeypatch):
    cid, _, ids = owner
    vid = ids["personal"]
    from test_publication_layout import activate

    from fiber_bundle.vault_save_contract import settings_wire, unified_settings

    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    activate(config, vid, **settings)
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *a: policy)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                client_id=cid,
                direct_to_inbox=True,
                processing_mode="pi_skill",
                model_configuration_id="synthetic",
                allow_model_source_content=True,
                allowed_kinds=["expert"],
            ),
        )
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic", limit=10000000),
            ),
        )
        raw = pdf_bytes(
            (
                "1 Technical reference\nA polymer network contains connected chains. "
                "The reported material property applies under the stated experimental conditions.",
            )
        )
        req = request(cid, vid, raw, "technical-book.pdf", "process", None)
        req.update(
            engine="fake-expert",
            expert_profile="native-text",
            expert_backend=None,
            workpack_import_id=None,
        )
        imported = upload(config, db, req, raw)
        expert_tasks.prepare(config, db, cid, imported["id"])
        expert_authoring.prepare(config, db, cid, imported["id"])
    return cid, vid, imported


def test_real_pi_loads_full_project_skill_and_compiler_publishes_once(
    config, owner, policy, monkeypatch
):
    from fiber_bundle.model_configuration.runtime import expected_image

    policy = replace(
        policy, image=expected_image(), max_calls=8, daily_calls=20, max_output_tokens=4096
    )
    _, marker = install(config)
    cid, vid, imported = setup_book(config, owner, policy, monkeypatch)
    requests = []
    per_run = {}

    def upstream(model, run_id, body):
        requests.append(body)
        encoded = canonical(body)
        assert marker in encoded, "Actual Pi did not load the selected complete SKILL.md"
        # The current fixed task prompt binds this evidence, independent of the provider.
        with database.connect(config.state_root) as db:
            binding = dict(
                db.execute("SELECT * FROM pi_processing_runs WHERE run_id=?", (run_id,)).fetchone()
            )
        inputs = json.loads(binding["input_json"])
        unit = inputs["evidence"][0]
        stage = per_run.get(binding["run_id"], 0)
        per_run[binding["run_id"]] = stage + 1
        calls = []
        if stage < 3:
            path = (
                "/task/input.json"
                if stage == 0
                else "/task/skills/fake-expert/references/quickstart.md"
            )
            tool, arguments = "read", {"path": path}
            if stage == 2:
                draft = {
                    "schema": "fiber-processing-draft/v1",
                    "kind": "expert",
                    "source_sha256": binding["source_sha256"],
                    "summary": "Source-bound chapter draft",
                    "evidence_ids": [unit["id"]],
                    "gaps": [],
                    "semantic_workpack": proposal(unit["id"], unit["focus_spans"][0]),
                }
                tool, arguments = "write", {"path": "/task/draft.json", "content": canonical(draft)}
            calls = [
                {
                    "id": "call-" + str(stage),
                    "type": "function",
                    "function": {"name": tool, "arguments": canonical(arguments)},
                }
            ]
        return {
            "id": "synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": model.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None if calls else "Draft saved",
                        **({"tool_calls": calls} if calls else {}),
                    },
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }

    monkeypatch.setattr(model_gateway, "complete", upstream)
    expert_authoring_runner.run(config, cid, imported["id"])
    for _ in range(4):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        task = expert_tasks.get(db, imported["id"])
        assert task["state"] == "completed", dict(task)
        assert expert_authoring.projection(db, imported["id"])["state"] == "complete"
        location = db.execute(
            "SELECT * FROM publication_locations WHERE kind='expert' AND publication_id=?",
            (imported["id"],),
        ).fetchone()
        assert location and location["state"] == "published"
        assert location["package_root"].startswith("Inbox/")
        assert json.loads(task["result_json"])["artifact_manifest"]["knowledge_verified"] is False
        calls = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
        assert calls == len(requests) and calls > 0
        receipts = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        for row in db.execute("SELECT * FROM pi_processing_runs"):
            pi_processing.materialize(config, dict(row))
            assert (
                json.loads(row["input_json"])["project_skill"] == imported["plan"]["project_skill"]
            )
    for _ in range(3):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == calls
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == receipts
    assert all(count == 4 for count in per_run.values())


def test_paused_book_resumes_old_skill_after_update_and_new_book_selects_new(
    config, owner, policy, monkeypatch
):
    import test_expert_authoring as original

    from fiber_bundle import expert_tool_versions, source_imports, source_processing_policy
    from fiber_bundle.store import Store

    configured = original.configured
    saved_budget = model_budgets.save
    changed = []
    verify = expert_tool_versions.verify

    def frozen_helpers(config, identity):
        root = verify(config, identity)
        saved = config.state_root / "expert-tool-snapshots" / identity
        return saved if saved.exists() else root

    # Exercise the actual frozen-source route used after a future helper upgrade,
    # including the worker overlay already present in project Skill mounts.
    monkeypatch.setattr(expert_tool_versions, "verify", frozen_helpers)

    def configure(config, runtime):
        result = configured(config, runtime)
        install(config)
        return result

    def update_between_attempts(db, request):
        result = saved_budget(db, request)
        if request.expected_revision == 2:
            path = config.project_root / "skills/fake-expert/SKILL.md"
            path.write_text(path.read_text() + "\nFIBER_PROJECT_SKILL_VERSION_B\n")
            changed.append(True)
        return result

    monkeypatch.setattr(original, "configured", configure)
    monkeypatch.setattr(model_budgets, "save", update_between_attempts)
    original.test_real_native_compiler_pi_proposals_and_draft_end_to_end(
        config, owner, policy, monkeypatch, True, True, True
    )
    assert changed == [True]
    cid, _, ids = owner
    with database.connect(config.state_root) as db:
        row = db.execute(
            "SELECT * FROM source_imports WHERE json_extract(plan_json,'$.engine')='fake-expert'"
        ).fetchone()
        old = json.loads(row["plan_json"])["project_skill"]
        assert expert_authoring.projection(db, row["id"])["state"] == "complete"
        assert all(
            json.loads(r[0])["project_skill"] == old
            for r in db.execute("SELECT input_json FROM pi_processing_runs")
        )
        count = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
        raw = Store(config).read_asset(row["asset_id"], vault="personal")
        settings = source_processing_policy.inspect(config, None)
        req = request(
            cid, ids["personal"], raw, "next-book.pdf", "process", settings["configuration_sha256"]
        )
        req.update(engine="fake-expert", expert_profile="native-text")
        new = upload(config, db, req, raw)
        assert new["plan"]["project_skill"]["snapshot_sha256"] != old["snapshot_sha256"]
        assert source_imports.create(config, db, json.loads(row["request_json"]))["id"] == row["id"]
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == count
