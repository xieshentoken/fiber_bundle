"""Captured article, model ledger and derived publication data run with only provider/Pi stubbed."""

import json
from unittest.mock import patch

from test_article_fetch import Reply, install
from test_articles import PNG, X_URL, current_html
from test_channel_intake import enable, receive, records
from test_channel_intake_content import grant_original
from test_publication_layout import activate

from fiber_bundle import automatic_policy, database, execution_ledger, model_gateway, pi_processing
from fiber_bundle.channels import intake_extraction, intake_processing
from fiber_bundle.channels.intake_pi import text_chunks

pytest_plugins = ["test_channel_intake", "test_execution"]


def test_chunks_preserve_utf8_without_silent_truncation():
    source = "汉字🙂 plain\n" * 10000
    chunks = text_chunks(source, 1000)
    assert "".join(chunks) == source
    assert all(len(c.encode()) <= 1000 for c in chunks)
    assert len(chunks) > 60


def test_article_pi_preserves_original_and_charges_actual_shared_ledger(
    config, entry, policy, monkeypatch
):
    cid, vid = entry[1].client_id, entry[1].vault_id
    grant_original(config, entry)
    from fiber_bundle import vault_save_contract

    layout = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    layout.pop("layout_mode")
    activate(config, vid, **layout)
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *args: policy)
    from fiber_bundle import model_budgets

    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=policy.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic", limit=1000000),
            ),
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
                processing_mode="pi_skill",
                model_configuration_id="opencodego-primary",
                allow_model_source_content=True,
                allowed_kinds=["intake"],
            ),
        )
    enable(config, entry, policy_version=2, fetch_articles=True, store_limit=0, download_limit=0)
    receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    calls, _ = install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )

    def execute(config, policy, rid, prompt):
        with database.connect(config.state_root) as db:
            execution_ledger.start(config, db, rid, policy)
            call = execution_ledger.reserve(config, db, rid, policy)
            execution_ledger.settle(
                db, call, policy, {"prompt_tokens": 20, "completion_tokens": 10}
            )
            binding = pi_processing.get(db, rid)
            pi_processing.materialize(config, binding)
            data = json.loads(binding["input_json"])
            path = config.state_root / "tasks" / rid / "draft.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "fiber-processing-draft/v1",
                        "kind": "article",
                        "source_sha256": binding["source_sha256"],
                        "summary": "Synthetic evidence-linked analysis",
                        "evidence_ids": [data["evidence"][0]["id"]],
                        "gaps": [],
                    }
                )
            )
            execution_ledger.transition(db, rid, "succeeded")
        return {"state": "succeeded"}

    monkeypatch.setattr("fiber_bundle.execution.execute", execute)
    from fiber_bundle.channels import intake_pi

    original_run = intake_pi.run
    errors = []

    def checked_run(*args):
        try:
            return original_run(*args)
        except Exception as error:
            errors.append(error)
            raise

    monkeypatch.setattr(intake_pi, "run", checked_run)
    with patch.object(model_gateway, "complete") as provider:
        assert intake_extraction.advance(config, entry[0]["id"])
        # Legacy analysis remains resumable, but capture no longer starts it.
        batch = records(config, entry)[0]["batch_id"]
        assert intake_pi.run(config, entry[0]["id"], batch, 0)
        provider.assert_not_called()
    assert not errors, [(type(e).__name__, str(e)) for e in errors]
    value = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert value["pi_processing"]["state"] == "complete", (
        value.get("reason"),
        value["pi_processing"],
    )
    assert value["state"] == "review_required"
    assert "analysis.md" in value["files"] and "note.md" in value["files"]
    assert len(calls) == 2
    assert not intake_extraction.advance(config, entry[0]["id"])
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 1
        assert (
            db.execute("SELECT sum(input_tokens+output_tokens) FROM model_calls").fetchone()[0]
            == 30
        )
