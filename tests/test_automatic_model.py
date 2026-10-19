"""Budgeted model reviews cannot invent authority, omit units, or replay uncertain calls."""

import json
from dataclasses import asdict
from unittest.mock import patch

import pytest
from test_automatic_ingestion import admitted
from test_automatic_ingestion import explicit_ticks as explicit_ticks
from test_execution import policy as policy
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner

from fiber_bundle import automatic_ingestion, automatic_jobs, automatic_policy, database, search


def model_job(config, owner, policy, tmp_path):
    # Enable before a new admission; existing job bindings must not be silently upgraded.
    from test_automatic_ingestion import setup

    cid, vid = setup(config, owner)
    path = tmp_path / "review-route.json"
    path.write_text(json.dumps(asdict(policy)))
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            vid,
            1,
            automatic_policy.Policy(
                client_id=cid,
                review_mode="automatic",
                publish_mode="automatic",
                review_method="model",
                allowed_kinds=["document"],
                model_config_path=str(path),
                allow_model_source_content=True,
            ),
        )
    with patch("test_automatic_ingestion.setup", return_value=(cid, vid)):
        return admitted(config, owner)


@pytest.mark.parametrize("fault", ["accept", "uncertain", "coverage", "timeout", "revoked"])
def test_model_review_is_bound_budgeted_and_not_replayed(
    config, store, owner, policy, tmp_path, fault
):
    cid, vid, _, job, rid = model_job(config, owner, policy, tmp_path)
    assert automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert automatic_jobs.get(db, job["id"])["phase"] == "model_review"
    calls = []

    def complete(policy, run, payload):
        calls.append(run)
        if fault == "timeout":
            raise TimeoutError()
        content = json.loads(payload["messages"][1]["content"])
        units = content["units"]
        if fault == "coverage":
            units = []
        if fault == "revoked":
            from fiber_bundle import auth

            with database.connect(config.state_root) as db:
                auth.set_grant(db, cid, vid, query=True, original=False, add=True)
        return {
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "decision": "uncertain" if fault == "uncertain" else "accept",
                                "reasons": ["synthetic review"],
                                "units": [{"unit_id": u, "decision": "accept"} for u in units],
                            }
                        )
                    }
                }
            ],
        }

    with patch("fiber_bundle.model_gateway.complete", side_effect=complete):
        assert automatic_ingestion.tick(config)
        for _ in range(4):
            automatic_ingestion.tick(config)
        automatic_ingestion.recover(config)
        automatic_ingestion.tick(config)
    assert len(calls) == 1
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM document_reviews").fetchone()[0] == 0
        final = automatic_jobs.get(db, job["id"])
        if fault == "accept":
            assert final["state"] == "complete", final
            hit = search.vault_search(config, db, cid, "verified-auto-token", [vid])["results"][0]
            assert hit["review_actor"] == "system_model"
            review = json.loads(db.execute("SELECT value FROM automatic_reviews").fetchone()[0])
            assert review["model_run_refs"][0]["run_id"] == calls[0]
        else:
            assert final["state"] in {"needs_review", "reconcile"}, final
            assert not db.execute("SELECT 1 FROM document_publications").fetchone()
