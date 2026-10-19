"""Fixed skill bindings preserve source identity and reject fabricated draft evidence."""

import json

import pytest

from fiber_bundle import database, execution_ledger, pi_processing, pi_skills
from fiber_bundle.config import IntegrityError, InvalidInput

pytest_plugins = ["test_execution"]


def test_three_bundled_skills_are_pinned_and_unknown_profile_is_rejected():
    assert {pi_skills.selected(k)["pi_version"] for k in ("article", "video", "expert")} == {
        "0.87.1"
    }
    with pytest.raises(InvalidInput, match="unknown"):
        pi_skills.selected("../../outside")


def test_bound_input_and_draft_preserve_source_and_require_real_evidence(config, policy, bound):
    run, client, vault = bound
    with database.connect(config.state_root) as db:
        version = execution_ledger.get_run(db, run)["version_id"]
        with pytest.raises(InvalidInput, match="not_allowed"):
            pi_processing.prepare(
                config,
                db,
                policy,
                client,
                vault,
                version,
                "article",
                "a" * 64,
                [{"id": "source:1", "text": "synthetic"}],
                allow_cloud=False,
            )
        rid = pi_processing.prepare(
            config,
            db,
            policy,
            client,
            vault,
            version,
            "article",
            "a" * 64,
            [{"id": "source:1", "text": "synthetic"}],
            allow_cloud=True,
        )
        binding = pi_processing.get(db, rid)
        pi_processing.materialize(config, binding)
        assert "secret" not in (config.state_root / "tasks" / rid / "input.json").read_text()
        execution_ledger.transition(db, rid, "succeeded")
        path = config.state_root / "tasks" / rid / "draft.json"
        value = {
            "schema": "fiber-processing-draft/v1",
            "kind": "article",
            "source_sha256": "a" * 64,
            "summary": "source-grounded synthetic draft",
            "evidence_ids": ["source:1"],
            "gaps": [],
        }
        path.write_text(json.dumps(value))
        assert pi_processing.result(config, db, rid)["knowledge_verified"] is False
        value["evidence_ids"] = ["made-up-id"]
        path.write_text(json.dumps(value))
        with pytest.raises(IntegrityError, match="evidence_changed"):
            pi_processing.result(config, db, rid)
        (path.parent / "input.json").write_text("{}")
        with pytest.raises(IntegrityError, match="input_changed"):
            pi_processing.materialize(config, binding)


def test_unified_processing_without_key_budget_pauses_before_any_provider_debit(
    config, policy, bound
):
    from fiber_bundle import model_budgets

    run, client, vault = bound
    with database.connect(config.state_root) as db:
        version = execution_ledger.get_run(db, run)["version_id"]
        rid = pi_processing.prepare(
            config,
            db,
            policy,
            client,
            vault,
            version,
            "video",
            "a" * 64,
            [{"id": "segment:1", "text": "evidence"}],
            allow_cloud=True,
        )
        execution_ledger.start(config, db, rid, policy)
        with pytest.raises(model_budgets.Exhausted, match="processing_key_budget_unconfigured"):
            execution_ledger.reserve(config, db, rid, policy)
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
