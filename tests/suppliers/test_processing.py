"""Real source/ledger boundaries with only document worker and model network replaced."""

import json
from unittest.mock import patch

import pytest
from document_support import candidate
from test_document_tasks import call, start
from test_document_tasks import document_scope as document_scope
from test_execution import policy as policy
from test_model_configuration import spec
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import (
    auth,
    database,
    document_capture,
    document_evidence,
    document_review,
    document_worker,
    model_gateway,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.document_worker import DocumentCapture
from fiber_bundle.inputs import canonical
from fiber_bundle.model_configuration import store as catalog
from fiber_bundle.suppliers import commands, ingestion, model, service
from fiber_bundle.suppliers import store as supplier_store
from fiber_bundle.suppliers.contracts import DocumentImport, Policy, SavePolicy

TEXT = "Synthetic supplier Diamond Alpha 590 CNY/100g"
REPLY = {
    "offers": [
        dict(
            supplier_name="Synthetic supplier",
            product_name="Diamond Alpha",
            model="Alpha",
            amount="590",
            currency="CNY",
            unit="100g",
            evidence_quote=TEXT,
        )
    ],
    "warnings": [],
}


def worker(config, policy, source, *, guard, task_id):
    guard()
    root = config.state_root / "document-captures" / task_id
    (root / "input").mkdir(parents=True)
    (root / "output").mkdir()
    document_capture.write_new(root / "input", "source.pdf", source)
    document_capture.write_new(
        root / "output", "result.json", canonical(candidate(source, TEXT)).encode()
    )
    return DocumentCapture(task_id, root, document_capture.verify(root / "output", source))


@pytest.fixture
def ready(config, policy, document_scope, vault_keychain):
    rid = start(config, policy, document_scope)
    with patch.object(document_worker, "execute_document", side_effect=worker):
        call(config, policy, rid)
    cid, vid, *_ = document_scope
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
        db.execute("UPDATE execution_runs SET state='succeeded' WHERE id=?", (rid,))
        metadata, parsed = document_evidence.load(config, db, cid, rid)
        document_review.record(
            db,
            rid,
            parsed,
            dict(
                source_sha256=metadata["source_sha256"],
                result_sha256=metadata["result_sha256"],
                source_reviewed=True,
                visual_reviewed=True,
                judgments=[
                    dict(unit_id="P1", verdict="accept", reason="Synthetic reviewed fixture")
                ],
            ),
        )
        selected = catalog.save(config, db, spec())
        service.save_policy(
            config,
            db,
            SavePolicy(
                client_id=cid,
                vault_id=vid,
                expected_revision=0,
                settings=Policy(
                    enabled=True,
                    cloud_allowed=True,
                    review_mode="automatic",
                    model_configuration_id=selected["id"],
                ),
            ),
        )
        job = ingestion.document(
            config, db, DocumentImport(client_id=cid, vault_id=vid, run_id=rid)
        )
    return cid, vid, job


def test_full_document_to_supplier_preserves_original_and_shared_usage(config, ready, monkeypatch):
    cid, vid, job = ready
    calls = []

    def complete(policy, identity, payload):
        calls.append(identity)
        assert TEXT in canonical(payload)
        return {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [{"message": {"content": canonical(REPLY)}}],
        }

    monkeypatch.setattr(model_gateway, "complete", complete)
    model.run(config, vid, job["id"])
    model.run(config, vid, job["id"])
    saved = supplier_store.get(config, vid, "job", job["id"])
    assert saved["state"] == "complete" and len(calls) == 1 and saved["model_sha256"]
    assert len(supplier_store.rows(config, vid, "quote")) == 1
    with database.connect(config.state_root) as db:
        rows = db.execute(
            "SELECT state,input_tokens,output_tokens,policy_json "
            "FROM model_configuration_calls WHERE id=?",
            (calls[0],),
        ).fetchall()
        assert len(rows) == 1 and tuple(rows[0][:3]) == ("succeeded", 100, 50)
        assert json.loads(rows[0]["policy_json"])["purpose"] == "supplier_extraction"
        raw, _, mime = __import__("fiber_bundle.suppliers.read", fromlist=["original"]).original(
            config, db, cid, vid, job["source"]["id"]
        )
        assert mime == "application/pdf" and raw.startswith(b"%PDF")
    assert not list((config.project_root / "test vault/temp_vault1").rglob("*.md"))


def test_unknown_network_result_is_never_replayed(config, ready, monkeypatch):
    _, vid, job = ready
    calls = []

    def fail(*args):
        calls.append(1)
        raise TimeoutError("Synthetic boundary timeout")

    monkeypatch.setattr(model_gateway, "complete", fail)
    model.run(config, vid, job["id"])
    model.run(config, vid, job["id"])
    current = supplier_store.get(config, vid, "job", job["id"])
    assert (
        current["state"] == "failed"
        and current["error"] == "supplier_model_result_unknown"
        and len(calls) == 1
    )
    assert not supplier_store.rows(config, vid, "quote")
    with (
        database.connect(config.state_root) as db,
        pytest.raises(InvalidInput, match="supplier_model_result_unknown"),
    ):
        commands.job_action(
            config, db, current["client_id"], vid, job["id"], commands.version(current), "resume"
        )
    assert (
        supplier_store.get(config, vid, "source", job["source"]["id"])["original_state"]
        == "retained"
    )


def test_cancel_during_network_retains_response_without_applying(config, ready, monkeypatch):
    cid, vid, job = ready

    def complete(*args):
        with database.locked(config), database.connect(config.state_root) as db:
            current = supplier_store.get(config, vid, "job", job["id"])
            commands.job_action(
                config, db, cid, vid, job["id"], commands.version(current), "cancel"
            )
        return {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "choices": [{"message": {"content": canonical(REPLY)}}],
        }

    monkeypatch.setattr(model_gateway, "complete", complete)
    model.run(config, vid, job["id"])
    assert supplier_store.get(config, vid, "job", job["id"])["state"] == "cancelled"
    assert not supplier_store.rows(config, vid, "quote")
    assert supplier_store.rows(config, vid, "model_call")[0]["state"] == "succeeded"


def test_manual_review_does_not_auto_apply(config, ready, monkeypatch):
    cid, vid, job = ready
    with database.connect(config.state_root) as db:
        profile = supplier_store.profile(config, vid)
        service.save_policy(
            config,
            db,
            SavePolicy(
                client_id=cid,
                vault_id=vid,
                expected_revision=profile["revision"],
                settings=Policy(**{**profile["settings"], "review_mode": "manual"}),
            ),
        )
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *args: {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "choices": [{"message": {"content": canonical(REPLY)}}],
        },
    )
    model.run(config, vid, job["id"])
    current = supplier_store.get(config, vid, "job", job["id"])
    assert current["state"] == "needs_review" and not supplier_store.rows(config, vid, "quote")
    with database.connect(config.state_root) as db:
        commands.job_action(config, db, cid, vid, job["id"], commands.version(current), "approve")
    assert len(supplier_store.rows(config, vid, "quote")) == 1


def test_restart_marks_inflight_call_unknown_without_replaying(config, ready, monkeypatch):
    from types import SimpleNamespace

    from fiber_bundle.suppliers.runtime import Runtime

    cid, vid, job = ready

    def interrupted(*args):
        raise SystemExit("Simulated process interruption")

    monkeypatch.setattr(model_gateway, "complete", interrupted)
    with pytest.raises(SystemExit):
        model.run(config, vid, job["id"])
    assert supplier_store.get(config, vid, "job", job["id"])["state"] == "running"
    monkeypatch.setattr(
        "fiber_bundle.suppliers.runtime.threading.Thread",
        lambda **kw: SimpleNamespace(start=lambda: None),
    )
    Runtime(config).start()
    current = supplier_store.get(config, vid, "job", job["id"])
    assert current["state"] == "failed"
    assert supplier_store.rows(config, vid, "model_call")[0]["state"] == "unknown"
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT state,error_code FROM model_configuration_calls").fetchone()[
            :
        ] == ("failed", "supplier_model_result_unknown")
        with pytest.raises(InvalidInput, match="supplier_model_result_unknown"):
            commands.job_action(
                config, db, cid, vid, job["id"], commands.version(current), "resume"
            )
