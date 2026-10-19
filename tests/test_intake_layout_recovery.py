"""Replaced directories stop publication, preserve originals, and resume the same batch."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, X_URL, current_html
from test_channel_intake import count, enable, receive, records
from test_channel_intake_content import grant_original
from test_publication_layout import activate

from fiber_bundle import (
    automatic_ingestion,
    automatic_policy,
    database,
    vault_save_contract,
    vault_save_policy,
)
from fiber_bundle.channels import (
    batch_intake,
    health,
    intake_actions,
    intake_contract,
    intake_extraction,
    intake_processing,
    intake_receipts,
    intake_runtime,
)
from fiber_bundle.channels.intake_failures import restore
from fiber_bundle.config import IntegrityError, InvalidInput

pytest_plugins = ["test_channel_intake", "test_execution"]


def configure(config, entry, policy, monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)
    monkeypatch.setattr(automatic_policy, "processing_route", lambda *a: policy)
    grant_original(config, entry)
    layout = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    layout.pop("layout_mode")
    activate(config, entry[1].vault_id, **layout)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            entry[1].vault_id,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                direct_to_inbox=True,
                client_id=entry[1].client_id,
                processing_mode="pi_skill",
                allow_model_source_content=True,
                allowed_kinds=["intake", "document"],
            ),
        )
    enable(config, entry, policy_version=2, receipt=True, fetch_articles=True)
    return layout


def test_directory_reconfirmation_resumes_original_batch_once(config, entry, policy, monkeypatch):
    layout = configure(config, entry, policy, monkeypatch)
    root = config.project_root / "test vault/temp_vault1"
    inbox = root / layout["inbox_dir"]
    inbox.rename(root / "kept-old-inbox")
    inbox.mkdir()
    batch = receive(config, entry, text=X_URL)
    with pytest.raises(InvalidInput, match="automatic_policy_layout_changed"):
        intake_processing.advance(config, entry[0]["id"])
    prior = records(config, entry)[0]
    value = json.loads(prior["value"])
    assert prior["state"] == "needs_review" and prior["reason"] == "automatic_policy_layout_changed"
    assert value["failure"]["phase"] == "processing_admission"
    assert value["failure"]["originals_saved"]
    assert count(config, "receipts") == 1 and count(config, "automatic_jobs") == 0
    assert intake_actions.inspect(config, entry[0]["id"])["blockers"] == [
        "automatic_policy_layout_changed"
    ]
    with database.connect(config.state_root) as db:
        status = health.present(config, db, entry[0])
    assert status["intake"]["reason"] == "automatic_policy_layout_changed"
    assert status["intake"]["pending_review"] == 1
    assert not status["capabilities"]["store"]["available"]

    sent = []

    def send(owner, body, guard):
        guard()
        sent.append(body)
        return {"category": "confirmed"}

    adapter = SimpleNamespace(_fiber_send_receipt=send)
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], adapter))
    asyncio.run(intake_receipts.deliver(config, entry[0]["id"], adapter))
    assert len(sent) == 1 and "原消息和原件已归档" in sent[0] and "保存规则" in sent[0]
    assert "消息已接收" not in sent[0]
    failure_receipts = json.loads(records(config, entry)[0]["value"])["receipt_updates"]

    activate(config, entry[1].vault_id, **layout)
    action = intake_contract.BatchAction(expected_revision=prior["revision"], batch_ids=[batch])
    intake_actions.select(config, entry[0]["id"], action)
    recovering = json.loads(records(config, entry)[0]["value"])
    assert recovering["failure_history"] == [value["failure"]]
    assert "failure" not in recovering and recovering["receipt_updates"] == failure_receipts
    assert recovering["receipts"] == value["receipts"]
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert count(config, "receipts") == 1 and count(config, "automatic_jobs") == 1
    intake_actions.select(config, entry[0]["id"], action)
    assert intake_processing.advance(config, entry[0]["id"]) == 0

    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    monkeypatch.setattr(
        "fiber_bundle.execution.execute",
        lambda *a: pytest.fail("must not repeat or add optional model work"),
    )
    assert intake_extraction.advance(config, entry[0]["id"])
    for _ in range(5):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT state FROM automatic_jobs").fetchone()[0] == "complete"
        assert db.execute("SELECT count(*) FROM publication_locations").fetchone()[0] == 1
        assert vault_save_policy.get(config, db, entry[1].vault_id)["settings"] == {
            "layout_mode": "obsidian_folders",
            **layout,
        }
    assert len(list(inbox.glob("*.md"))) == 1
    assert count(config, "model_calls") == 0
    assert (root / "kept-old-inbox").is_dir()
    assert batch_intake.worker_status(config, entry[0])["state"] == "unobserved"


def test_link_only_admission_never_requires_unrelated_document_model(
    config, entry, policy, monkeypatch
):
    configure(config, entry, policy, monkeypatch)

    def missing(*a):
        raise InvalidInput("processing_model_unconfigured")

    monkeypatch.setattr(automatic_policy, "processing_route", missing)
    receive(config, entry, text=X_URL)
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert records(config, entry)[0]["state"] == "stored"
    assert count(config, "automatic_jobs") == 1 and count(config, "model_calls") == 0


def test_publish_failure_is_precise_and_resume_reuses_capture(config, entry, policy, monkeypatch):
    from fiber_bundle import automatic_recovery, publication_layout

    configure(config, entry, policy, monkeypatch)
    receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    install(
        monkeypatch,
        [
            Reply(200, current_html(), [("Content-Type", "text/html")]),
            Reply(200, PNG, [("Content-Type", "image/png")]),
        ],
    )
    intake_extraction.advance(config, entry[0]["id"])
    captured = json.loads(records(config, entry)[0]["value"])["links"][0]
    automatic_ingestion.tick(config)
    with monkeypatch.context() as failure:

        def exhausted(*args, **kwargs):
            raise IntegrityError("layout_directory_limit")

        failure.setattr(publication_layout, "prepare", exhausted)
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        job = db.execute("SELECT * FROM automatic_jobs").fetchone()
        assert job["state"] == "reconcile" and job["reason"] == "layout_directory_limit"
        before = db.execute("SELECT count(*) FROM receipts").fetchone()[0]
        resumed = automatic_recovery.resume(config, db, entry[1].vault_id, job["id"])
        assert resumed["phase"] == "publish" and not resumed["new_model_attempt"]
    for _ in range(4):
        automatic_ingestion.tick(config)
    with database.connect(config.state_root) as db:
        after = db.execute("SELECT * FROM automatic_jobs").fetchone()
        assert after["id"] == job["id"] and after["state"] == "complete"
        history = json.loads(after["value"])["failure_history"]
        assert history[0]["reason"] == "layout_directory_limit"
        assert history[0]["failure"]["phase"] == "publish"
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == before
    current = json.loads(records(config, entry)[0]["value"])["links"][0]
    for key in ("attempt_id", "receipt", "files", "metadata"):
        assert current[key] == captured[key]
    assert count(config, "model_calls") == 0


def test_explicit_recovery_keeps_completed_links_and_unknown_notification():
    links = [
        {
            "original_url": X_URL,
            "state": "review_required",
            "run_id": "existing",
            "pi_processing": {"state": "complete"},
        }
    ]
    old = {
        "links": links,
        "receipt_state": "unknown",
        "receipt_updates": {"received": {"state": "unknown"}},
        "receipt_tracking": {"version": 2, "done": True},
    }
    new = {"links": [{"original_url": X_URL}]}
    restore(new, old, "intake_archive_interrupted")
    assert new["links"] == links and new["receipt_state"] == "unknown"
    assert new["receipt_updates"] == old["receipt_updates"]
    assert not new["receipt_tracking"]["done"]
    with pytest.raises(InvalidInput, match="intake_batch_changed"):
        restore({"links": [{"original_url": X_URL + "-changed"}]}, old, None)


def test_archived_record_still_reports_failed_extraction(config, entry, policy, monkeypatch):
    from fiber_bundle.channels.intake_health import pending_review

    configure(config, entry, policy, monkeypatch)
    batch = receive(config, entry, text=X_URL)
    intake_processing.advance(config, entry[0]["id"])
    intake_extraction.update(
        config,
        entry[0]["id"],
        batch,
        0,
        {"state": "needs_review", "reason": "intake_extractor_unavailable"},
    )
    assert records(config, entry)[0]["state"] == "stored"
    assert pending_review(config, entry[0]["id"]) == 1
    with database.connect(config.state_root) as db:
        db.execute("UPDATE automatic_jobs SET state='cancelled'")
    assert pending_review(config, entry[0]["id"]) == 0
    # Cancellation remains history; it never clears the original extraction diagnosis.
    link = json.loads(records(config, entry)[0]["value"])["links"][0]
    assert link["reason"] == "intake_extractor_unavailable"


def test_idle_tick_keeps_unresolved_batches_visible(config, entry, policy, monkeypatch):
    layout = configure(config, entry, policy, monkeypatch)
    root = config.project_root / "test vault/temp_vault1"
    path = root / layout["inbox_dir"]
    path.rename(root / "old-inbox")
    path.mkdir()
    receive(config, entry, text=X_URL)
    adapter = SimpleNamespace(_fiber_send_receipt=lambda *a: {"category": "confirmed"})
    assert (
        asyncio.run(intake_runtime.tick(config, entry[0]["id"], adapter))
        == "intake_requires_review"
    )
    assert (
        asyncio.run(intake_runtime.tick(config, entry[0]["id"], adapter))
        == "intake_requires_review"
    )
    assert count(config, "receipts") == 1 and count(config, "automatic_jobs") == 0


def test_worker_readiness_rejects_stale_previous_process_and_bad_status(config, entry):
    home = config.state_root / "configs/hermes-feishu-synthetic"
    path = home / "intake-status.json"
    path.write_text(json.dumps({"state": "ready", "observed_at": time.time() - 60}))
    assert not batch_intake.worker_status(config, entry[0])["fresh"]
    (home / "fiber-status.json").write_text(json.dumps({"started_at": time.time() + 1}))
    path.write_text(json.dumps({"state": "ready", "observed_at": time.time()}))
    assert not batch_intake.worker_status(config, entry[0])["fresh"]
    path.write_text("[]")
    assert batch_intake.worker_status(config, entry[0])["state"] == "unobserved"
