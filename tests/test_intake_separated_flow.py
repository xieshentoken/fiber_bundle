"""Extraction, human review and model enrichment are independent choices."""

import json

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, X_URL, current_html
from test_channel_intake import enable, receive, records
from test_channel_intake_content import grant_original
from test_publication_layout import activate

from fiber_bundle import automatic_ingestion, automatic_policy, database, vault_save_contract
from fiber_bundle.channels import intake_extraction, intake_processing

pytest_plugins = ["test_channel_intake", "test_execution"]


@pytest.mark.parametrize("mode", ["basic", "pi_skill"])
@pytest.mark.parametrize("direct", [False, True])
def test_captured_article_needs_no_optional_model_before_publication(
    config, entry, policy, monkeypatch, mode, direct
):
    monkeypatch.setattr(
        "fiber_bundle.execution.execute", lambda *a: pytest.fail("unexpected optional model")
    )
    capture(config, entry, policy, monkeypatch, mode, direct)
    monkeypatch.setattr(
        automatic_policy,
        "processing_route",
        lambda *a: pytest.fail("optional model must not gate source publication"),
    )
    for _ in range(5):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        job = db.execute("SELECT * FROM automatic_jobs").fetchone()
        assert job["state"] == ("complete" if direct else "reviewed")
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM execution_runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == int(direct)


def capture(config, entry, policy, monkeypatch, mode, direct, extra=False):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *a: policy)
    grant_original(config, entry)
    cid, vid = entry[1].client_id, entry[1].vault_id
    layout = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    layout.pop("layout_mode")
    activate(config, vid, **layout)
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
                direct_to_inbox=direct,
                processing_mode=mode,
                extra_summary=extra,
                allow_model_source_content=True,
                allowed_kinds=["intake"],
            ),
        )
    enable(config, entry, policy_version=2, fetch_articles=True)
    batch = receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["state"] == "review_required"
    assert "pi_processing" not in link and "analysis.md" not in link["files"]
    return batch


def test_optional_summary_defaults_off_even_for_intelligent_processing():
    assert not automatic_policy.Policy(processing_mode="pi_skill").extra_summary
