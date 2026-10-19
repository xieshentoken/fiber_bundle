"""Fixed compiler seed/validate/compile with synthetic Pi proposals and actual usage accounting."""

import json
import os
from pathlib import Path

import pytest
from document_support import pdf_bytes
from test_expert_runtime import configured
from test_source_imports import request, upload

from fiber_bundle import (
    automatic_policy,
    database,
    execution_ledger,
    expert_authoring,
    expert_authoring_runner,
    expert_tasks,
    model_budgets,
    pi_processing,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.expert_proposals import fragments, validate
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.query_policy_store import write_private

pytest_plugins = ["test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_progression_only(monkeypatch):
    # All paid execution is a synthetic callback in these tests. No background launcher.
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


def proposal(unit, span):
    return {
        "schema_version": "tkc.semantic-draft/v0.1",
        "unit_id": unit,
        "proposer_instance": "synthetic-proposer",
        "candidate_disposition": {
            "action": "promote",
            "reason": "Explicit source evidence; deferred review.",
        },
        "objects": [
            {
                "local_id": "claim",
                "type": "Claim",
                "origin": "source-paraphrase",
                "title": "Synthetic evidence",
                "statement": span["text"],
                "evidence_spans": [{k: span[k] for k in ("page", "start", "end", "span_sha256")}],
                "assumptions": [],
                "valid_when": [],
                "fails_when": [],
                "applicability": {
                    "physical_pages": [span["page"], span["page"]],
                    "excluded_conclusions": [],
                },
                "visual_task_ids": [],
                "formula": None,
            }
        ],
        "relations": [],
        "conflicts": [],
        "gaps": [],
    }


def test_fragments_cover_exact_source_and_reject_fabricated_anchor():
    text = "source evidence 中文 " * 3000
    unit = {
        "unit_id": "swu-" + "a" * 20,
        "title": "Synthetic",
        "focus_spans": [
            {
                "page": 1,
                "start": 0,
                "end": len(text),
                "text": text,
                "span_sha256": digest(text.encode()),
                "extractable": True,
            }
        ],
        "context_spans": [],
        "visual_task_ids": [],
    }
    parts = fragments(unit)
    assert "".join(p["focus_spans"][0]["text"] for p in parts) == text
    assert all(len(p["focus_spans"][0]["text"].encode()) <= 12000 for p in parts)
    good = proposal(unit["unit_id"], parts[0]["focus_spans"][0])
    assert validate(good, parts[0], "run")["proposer_instance"] == "fiber-pi:run"
    good["objects"][0]["evidence_spans"][0]["span_sha256"] = "b" * 64
    with pytest.raises(InvalidInput, match="source_changed"):
        validate(good, parts[0], "run")


@pytest.mark.skipif(not os.getenv("FIBER_EXPERT_RUNTIME"), reason="fixed Docker required")
def test_legacy_paused_task_executes_original_helpers_after_upgrade(
    config, owner, policy, monkeypatch
):
    from fiber_bundle import expert_tool_versions

    legacy = "27fe6e72ef3b733d1b1d3dc1b691a43f69fc656335a64769cc309a3b5fa956b4"
    monkeypatch.setattr(expert_tool_versions, "capture", lambda config: legacy)
    test_real_native_compiler_pi_proposals_and_draft_end_to_end(
        config, owner, policy, monkeypatch, True, True, True
    )
    with database.connect(config.state_root) as db:
        value = json.loads(
            db.execute(
                "SELECT payload FROM vault_operations WHERE kind='expert_authoring'"
            ).fetchone()[0]
        )
        assert value["tool_sha256"] == legacy


@pytest.mark.skipif(
    not os.getenv("FIBER_EXPERT_RUNTIME"), reason="explicit installed compiler runtime required"
)
@pytest.mark.parametrize(
    "check,direct,pause_budget",
    [
        (True, False, False),
        (True, True, False),
        (False, False, False),
        (False, True, False),
        (True, False, True),
    ],
)
def test_real_native_compiler_pi_proposals_and_draft_end_to_end(
    config, owner, policy, monkeypatch, check, direct, pause_budget, visual_review=False
):
    cid, _, ids = owner
    vid = ids["personal"]
    runtime = json.loads(Path(os.environ["FIBER_EXPERT_RUNTIME"]).read_bytes())
    settings = configured(config, runtime)
    from test_publication_layout import activate

    from fiber_bundle.vault_save_contract import settings_wire, unified_settings

    layout = settings_wire(unified_settings())
    layout.pop("layout_mode")
    activate(config, vid, **layout)
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *args: policy)
    raw = pdf_bytes(
        (
            "1 Introduction\nSynthetic evidence contains enough alphabetic text "
            "to support a bounded source claim.",
            "2 Conditions\nAdditional synthetic evidence describes a separate condition "
            "and preserves source traceability.",
        )
    )
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
                completeness_check=check,
                direct_to_inbox=direct,
                processing_mode="pi_skill",
                model_configuration_id="opencodego-primary",
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
        req = request(cid, vid, raw, "book.pdf", "process", settings["configuration_sha256"])
        req.update(
            engine="fake-expert",
            expert_profile="native-text",
            expert_backend=None,
            workpack_import_id=None,
        )
        imported = upload(config, db, req, raw)
        identity = imported["id"]
        expert_tasks.prepare(config, db, cid, identity)
        expert_authoring.prepare(config, db, cid, identity)

    attempts = []

    def execute(config, model, rid, prompt):
        with database.connect(config.state_root) as db:
            execution_ledger.start(config, db, rid, model)
            attempts.append(rid)
            if pause_budget and len(attempts) == 2:
                model_budgets.save(
                    db,
                    model_budgets.Save(
                        secret_file=policy.secret_file,
                        expected_revision=1,
                        settings=model_budgets.Policy(label="Synthetic", limit=0),
                    ),
                )
            try:
                call = execution_ledger.reserve(config, db, rid, model)
            except model_budgets.Exhausted:
                execution_ledger.transition(db, rid, "waiting_budget")
                return
            execution_ledger.settle(db, call, model, {"prompt_tokens": 20, "completion_tokens": 10})
            binding = pi_processing.get(db, rid)
            pi_processing.materialize(config, binding)
            data = json.loads(binding["input_json"])
            unit = data["evidence"][0]
            span = unit["focus_spans"][0]
            write_private(
                config.state_root / "tasks" / rid / "draft.json",
                canonical(
                    {
                        "schema": "fiber-processing-draft/v1",
                        "kind": "expert",
                        "source_sha256": binding["source_sha256"],
                        "summary": "Synthetic chapter proposal",
                        "evidence_ids": [unit["id"]],
                        "gaps": [],
                        "semantic_workpack": proposal(unit["id"], span),
                    }
                ).encode(),
            )
            execution_ledger.transition(db, rid, "succeeded")

    monkeypatch.setattr("fiber_bundle.execution.execute", execute)
    expert_authoring_runner.run(config, cid, identity)
    if pause_budget:
        with database.connect(config.state_root) as db:
            current = expert_authoring.projection(db, identity)
            assert current["state"] == "waiting_budget"
            assert current["completed_fragments"] == 1
            assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 1
            model_budgets.save(
                db,
                model_budgets.Save(
                    secret_file=policy.secret_file,
                    expected_revision=2,
                    settings=model_budgets.Policy(label="Synthetic", limit=10000000),
                ),
            )
        expert_authoring_runner.run(config, cid, identity)
        assert attempts.count(attempts[0]) == 1
        assert attempts[-1] == attempts[-2]
    with database.connect(config.state_root) as db:
        current = expert_tasks.get(db, identity)
        assert current["state"] == "completed", dict(current)
        authoring = expert_authoring.projection(db, identity)
        assert authoring["state"] == "complete" and authoring["completed_units"] > 0
        assert expert_tasks.projection(db, identity)["model_calls"] > 0
        assert (
            json.loads(current["result_json"])["artifact_manifest"]["knowledge_verified"] is False
        )
        assert (
            db.execute("SELECT sum(input_tokens+output_tokens) FROM model_calls").fetchone()[0]
            >= 30
        )

    from fiber_bundle import automatic_ingestion, automatic_jobs

    for _ in range(4):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        job = db.execute(
            "SELECT id FROM automatic_jobs WHERE kind='expert' AND source_id=?", (identity,)
        ).fetchone()
        current = automatic_jobs.get(db, job[0])
        if visual_review:
            assert current["state"] == "needs_review", current
            return
        assert current["state"] == ("complete" if direct else "reviewed"), current
        review = json.loads(
            db.execute("SELECT value FROM automatic_reviews WHERE job_id=?", (job[0],)).fetchone()[
                0
            ]
        )
        assert review["completeness_status"] == ("passed" if check else "not_checked")
        if direct:
            published = db.execute(
                "SELECT entry_note_path FROM publication_locations "
                "WHERE kind='expert' AND publication_id=?",
                (identity,),
            ).fetchone()
            assert published[0].startswith("Inbox/")
            assert db.execute("SELECT count(*) FROM document_reviews").fetchone()[0] == 0
