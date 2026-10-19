"""Optional enrichment failures cannot undo successful source publication."""

import json

import pytest
from test_intake_separated_flow import capture

from fiber_bundle import (
    automatic_ingestion,
    automatic_jobs,
    automatic_recovery,
    database,
    execution_ledger,
    model_budgets,
    pi_processing,
    search,
)
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake", "test_execution"]


@pytest.mark.parametrize("failure", [False, True, "invalid"])
def test_summary_runs_only_after_source_and_has_independent_failure(
    config, entry, policy, monkeypatch, failure
):
    capture(config, entry, policy, monkeypatch, "pi_skill", True, extra=True)
    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic summary", limit=1000000),
            ),
        )
    executions = []

    def execute(config, policy, rid, prompt):
        executions.append(rid)
        with database.connect(config.state_root) as db:
            execution_ledger.start(config, db, rid, policy)
            call = execution_ledger.reserve(config, db, rid, policy)
            if failure is True:
                execution_ledger.transition(db, rid, "failed", "model_timeout")
                return
            binding = pi_processing.get(db, rid)
            pi_processing.materialize(config, binding)
            data = json.loads(binding["input_json"])
            (config.state_root / "tasks" / rid / "draft.json").write_text(
                json.dumps(
                    {
                        "schema": "fiber-processing-draft/v1",
                        "kind": "article",
                        "source_sha256": binding["source_sha256"],
                        "summary": "Independent synthesis",
                        "gaps": [],
                        "evidence_ids": [data["evidence"][0]["id"]],
                    }
                )
            )
            if failure == "invalid":
                (config.state_root / "tasks" / rid / "draft.json").write_text("invalid response")
            execution_ledger.settle(
                db, call, policy, {"prompt_tokens": 20, "completion_tokens": 10}
            )
            execution_ledger.transition(db, rid, "succeeded")

    monkeypatch.setattr("fiber_bundle.execution.execute", execute)
    for _ in range(3):
        assert automatic_ingestion.tick(config)
    assert not executions
    with database.connect(config.state_root) as db:
        parent = dict(db.execute("SELECT * FROM automatic_jobs WHERE kind='intake'").fetchone())
        assert parent["state"] == "complete"
        location = db.execute("SELECT * FROM publication_locations").fetchone()
        note = config.vault_path("test vault/temp_vault1") / location["entry_note_path"]
        before = note.read_bytes()
    for _ in range(3):
        automatic_ingestion.tick(config)
    assert len(executions) == 1
    assert note.read_bytes() == before
    with database.connect(config.state_root) as db:
        assert (
            dict(db.execute("SELECT * FROM automatic_jobs WHERE kind='intake'").fetchone())
            == parent
        )
        child = dict(
            db.execute("SELECT * FROM automatic_jobs WHERE kind='intake_summary'").fetchone()
        )
        assert child["state"] == ("needs_review" if failure else "complete")
        assert automatic_jobs.display_state(child) == ("failed" if failure else "complete")
        if not failure:
            hits = search.vault_search(
                config, db, entry[1].client_id, "Independent synthesis", [entry[1].vault_id]
            )["results"]
            assert hits and hits[0]["source_kind"] == "agent_analysis"
            assert hits[0]["independent_evidence"] is False
    assert not automatic_ingestion.tick(config)
    if failure:
        with database.connect(config.state_root) as db:
            listing = automatic_jobs.listing(db, entry[1].vault_id)
            assert next(j for j in listing if j["kind"] == "intake_summary")[
                "requires_new_model_attempt"
            ]
            with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
                automatic_recovery.resume(config, db, entry[1].vault_id, child["id"])
            automatic_recovery.resume(
                config, db, entry[1].vault_id, child["id"], approve_new_model_attempt=True
            )
        failure = False
        for _ in range(3):
            automatic_ingestion.tick(config)
        assert len(executions) == 2 and executions[0] != executions[1]
        assert note.read_bytes() == before
        with database.connect(config.state_root) as db:
            assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2
            assert automatic_jobs.get(db, child["id"])["state"] == "complete"
