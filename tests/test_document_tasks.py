import base64
import json
import time
from dataclasses import replace
from unittest.mock import patch

import pytest
from document_support import candidate, pdf_bytes
from test_execution import policy as policy

from fiber_bundle import (
    auth,
    database,
    document_capture,
    document_tasks,
    document_worker,
    execution,
    model_gateway,
)
from fiber_bundle import document_bindings as bindings
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_contract import DocumentError
from fiber_bundle.document_worker import DocumentCapture, DocumentPolicy
from fiber_bundle.inputs import canonical
from fiber_bundle.operations import intake, original
from fiber_bundle.payloads import Intake

WORKER = DocumentPolicy("sha256:" + "b" * 64)


@pytest.fixture
def document_scope(config, store, policy):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-document-owner", False)
    vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vault, query=False, original=False, add=True)
    source = pdf_bytes()
    receipt = intake(
        config,
        db,
        client["id"],
        Intake(
            vault_id=vault,
            event_id="doc-event",
            source_key="doc-source",
            session_id="synthetic",
            raw_event=base64.b64encode(b"synthetic").decode(),
            assets=[
                {
                    "role": "attachment",
                    "media_type": "application/pdf",
                    "original_name": "private-name.pdf",
                    "data": base64.b64encode(source).decode(),
                }
            ],
        ),
    )
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=?", (receipt["source_version_id"],)
    ).fetchone()[0]
    db.close()
    return client["id"], vault, receipt, asset, source


def create(config, policy, scope):
    cid, vid, receipt, asset, _ = scope
    db = database.connect(config.state_root)
    try:
        return ledger.create_run(
            config,
            db,
            policy,
            cid,
            vid,
            receipt["source_version_id"],
            document=bindings.DocumentInput(receipt["job_id"], asset, WORKER),
        )
    finally:
        db.close()


def start(config, policy, scope):
    rid = create(config, policy, scope)
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    db.close()
    return rid


def worker_fixture(config, policy, source, *, guard, task_id):
    guard()
    root = config.state_root / "document-captures" / task_id
    for name in ("input", "output"):
        (root / name).mkdir(parents=True)
    document_capture.write_new(root / "input", "source.pdf", source)
    document_capture.write_new(
        root / "output", "result.json", canonical(candidate(source)).encode()
    )
    guard()
    return DocumentCapture(task_id, root, document_capture.verify(root / "output", source))


def call(config, policy, rid, body=None):
    return execution.handle(
        config,
        policy,
        rid,
        {"type": "document_extract", "id": "doc-call", "body": {} if body is None else body},
    )


@pytest.mark.parametrize(
    "query,download,add",
    [(q, o, a) for q in (False, True) for o in (False, True) for a in (False, True)],
)
def test_permission_matrix(config, policy, document_scope, query, download, add):
    db = database.connect(config.state_root)
    auth.set_grant(
        db, document_scope[0], document_scope[1], query=query, original=download, add=add
    )
    db.close()
    if add:
        assert create(config, policy, document_scope)
    else:
        with pytest.raises(auth.Denied):
            create(config, policy, document_scope)


def test_add_only_tool_reply_is_content_free_and_cached_without_reexecution(
    config, policy, document_scope
):
    rid = start(config, policy, document_scope)
    with patch.object(document_worker, "execute_document", side_effect=worker_fixture) as worker:
        first = call(config, policy, rid)
        assert call(config, policy, rid) == first
        assert worker.call_count == 1
    assert first["body"] == dict(
        state="extracted",
        page_count=1,
        ocr_page_count=0,
        review_page_count=0,
        published=False,
        knowledge_verified=False,
    )
    text = json.dumps(first)
    assert (
        document_scope[3] not in text
        and "private-name" not in text
        and "Synthetic original" not in text
    )
    db = database.connect(config.state_root)
    with pytest.raises(auth.Denied):
        original(
            config,
            db,
            document_scope[0],
            document_scope[1],
            document_scope[2]["source_version_id"],
            document_scope[3],
            10000,
        )
    db.close()
    with pytest.raises(InvalidInput):
        execution.handle(config, policy, rid, {"type": "vaults", "id": "v", "body": {}})


@pytest.mark.parametrize(
    "kind",
    [
        "other_client",
        "other_vault",
        "asset",
        "version",
        "incomplete",
        "mime",
        "role",
        "derived_weixin",
    ],
)
def test_wrong_source_bindings_roll_back_entire_run(config, policy, document_scope, kind):
    cid, vid, receipt, asset, source = document_scope
    db = database.connect(config.state_root)
    if kind == "other_client":
        other = auth.create_client(db, "other", False)
        auth.set_grant(db, other["id"], vid, query=True, original=True, add=True)
        cid = other["id"]
    elif kind == "other_vault":
        vid = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
        auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    elif kind == "asset":
        asset = "unknown-asset"
    elif kind == "version":
        other = intake(
            config,
            db,
            cid,
            Intake(
                vault_id=vid,
                event_id="other",
                source_key="other",
                session_id="s",
                raw_event=base64.b64encode(b"other").decode(),
                assets=[
                    {
                        "role": "body",
                        "media_type": "text/plain",
                        "original_name": "other.txt",
                        "data": base64.b64encode(b"other").decode(),
                    }
                ],
            ),
        )
        receipt = {**receipt, "source_version_id": other["source_version_id"]}
    elif kind == "incomplete":
        db.execute("UPDATE receipts SET state='incomplete' WHERE job_id=?", (receipt["job_id"],))
    elif kind in ("mime", "role"):
        column = "media_type" if kind == "mime" else "role"
        db.execute(
            f"UPDATE version_assets SET {column}=? WHERE asset_id=?",
            ("text/plain" if kind == "mime" else "event", asset),
        )
    else:
        db.execute(
            "UPDATE receipts SET event_id='weixin-task:unsupported' WHERE job_id=?",
            (receipt["job_id"],),
        )
    db.close()
    with pytest.raises((auth.Denied, InvalidInput, IntegrityError)):
        create(config, policy, (cid, vid, receipt, asset, source))
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM execution_runs").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM document_tasks").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize(
    "body", [{"asset_id": "other"}, {"path": "/private"}, {"vault_id": "other"}, [], True]
)
def test_tool_cannot_override_scope(config, policy, document_scope, body):
    rid = start(config, policy, document_scope)
    with patch.object(document_worker, "execute_document") as worker:
        with pytest.raises(InvalidInput):
            call(config, policy, rid, body)
        worker.assert_not_called()


@pytest.mark.parametrize("kind", ["client", "add", "original_bytes", "version_link", "policy"])
def test_changes_refuse_tool_and_model_before_egress(config, policy, document_scope, kind):
    rid = start(config, policy, document_scope)
    db = database.connect(config.state_root)
    if kind == "client":
        auth.revoke_client(db, document_scope[0])
    elif kind == "add":
        auth.set_grant(
            db, document_scope[0], document_scope[1], query=True, original=True, add=False
        )
    elif kind == "original_bytes":
        row = db.execute(
            "SELECT relative_path FROM assets WHERE id=?", (document_scope[3],)
        ).fetchone()
        (config.state_root / row[0]).write_bytes(b"changed")
    elif kind == "version_link":
        db.execute("DELETE FROM version_assets WHERE asset_id=?", (document_scope[3],))
    else:
        policy = replace(policy, price_revision="different")
    db.close()
    with (
        patch.object(document_worker, "execute_document") as worker,
        patch.object(model_gateway, "complete") as provider,
    ):
        for frame in [
            {"type": "document_extract", "id": "d", "body": {}},
            {"type": "model", "id": "m", "body": {}},
        ]:
            with pytest.raises((InvalidInput, IntegrityError, auth.Denied)):
                execution.handle(config, policy, rid, frame)
        worker.assert_not_called()
        provider.assert_not_called()


def test_model_request_overwrites_pi_scope_and_contains_no_document_data(
    config, policy, document_scope
):
    rid = start(config, policy, document_scope)
    response = {"usage": {"prompt_tokens": 10, "completion_tokens": 1}}
    with patch.object(model_gateway, "complete", return_value=response) as provider:
        execution.handle(
            config,
            policy,
            rid,
            {
                "type": "model",
                "id": "m",
                "body": {
                    "messages": [{"role": "user", "content": "PRIVATE_SENTINEL"}],
                    "tools": [{"name": "bash"}],
                },
            },
        )
    payload = provider.call_args.args[2]
    assert payload["messages"] == [{"role": "user", "content": bindings.PROMPT}]
    assert payload["tools"] == [bindings.TOOL]
    encoded = json.dumps(payload)
    for secret in [
        "PRIVATE_SENTINEL",
        "private-name.pdf",
        document_scope[3],
        document_scope[2]["job_id"],
    ]:
        assert secret not in encoded


@pytest.mark.parametrize("kind", ["cancel", "revoke", "deadline", "failure"])
def test_worker_failure_or_revocation_preserves_attempt_without_replay(
    config, policy, document_scope, kind
):
    rid = start(config, policy, document_scope)
    expired = False
    real_clock = time.monotonic

    def interrupted(*args, **kwargs):
        nonlocal expired
        result = worker_fixture(*args, **kwargs)
        db = database.connect(config.state_root)
        if kind == "cancel":
            ledger.transition(db, rid, "cancelled", "cancelled")
        elif kind == "revoke":
            auth.set_grant(
                db, document_scope[0], document_scope[1], query=False, original=False, add=False
            )
        db.close()
        if kind == "deadline":
            expired = True
        if kind == "failure":
            raise DocumentError("synthetic worker failure")
        kwargs["guard"]()
        return result

    with (
        patch.object(document_worker, "execute_document", side_effect=interrupted) as worker,
        patch.object(
            document_tasks.time,
            "monotonic",
            side_effect=lambda: (2000 if expired else 1000) if kind == "deadline" else real_clock(),
        ),
    ):
        with pytest.raises((DocumentError, InvalidInput, auth.Denied)):
            execution.handle(
                config,
                policy,
                rid,
                {"type": "document_extract", "id": "d", "body": {}},
                deadline=time.monotonic() + (0.02 if kind == "deadline" else 10),
            )
        with pytest.raises((InvalidInput, auth.Denied)):
            call(config, policy, rid)
        assert worker.call_count == 1
    assert (
        next((config.state_root / "document-captures").glob("*/input/source.pdf")).read_bytes()
        == document_scope[4]
    )
    db = database.connect(config.state_root)
    assert bindings.get(db, rid)["state"] == "failed"
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("kind", ["result", "original", "summary"])
def test_cached_candidate_tampering_refuses_reuse_and_completion(
    config, policy, document_scope, kind
):
    rid = start(config, policy, document_scope)
    with patch.object(document_worker, "execute_document", side_effect=worker_fixture):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    task = bindings.get(db, rid)
    root = config.state_root / "document-captures" / task["capture_task_id"]
    if kind == "result":
        (root / "output/result.json").write_bytes(
            canonical(
                candidate(
                    document_scope[4],
                    "Different but valid original text with enough supporting alphabetic evidence.",
                )
            ).encode()
        )
    elif kind == "original":
        (root / "input/source.pdf").write_bytes(b"changed")
    else:
        db.execute("UPDATE document_tasks SET page_count=2 WHERE run_id=?", (rid,))
    with pytest.raises((IntegrityError, DocumentError)):
        document_tasks.verify_complete(config, db, ledger.get_run(db, rid))
    db.close()
    with patch.object(document_worker, "execute_document") as worker:
        with pytest.raises((IntegrityError, DocumentError)):
            call(config, policy, rid)
        worker.assert_not_called()


def test_recover_interrupts_claimed_attempt_and_cannot_restart(config, policy, document_scope):
    rid = start(config, policy, document_scope)
    db = database.connect(config.state_root)
    db.execute("UPDATE document_tasks SET state='extracting' WHERE run_id=?", (rid,))
    capture_id = bindings.get(db, rid)["capture_task_id"]
    db.close()
    with patch.object(execution, "stop"), patch.object(document_worker, "stop_owned") as cleanup:
        execution.recover(config, rid)
    assert cleanup.call_args.args[2] == capture_id
    db = database.connect(config.state_root)
    assert bindings.get(db, rid)["state"] == "interrupted"
    assert ledger.get_run(db, rid)["state"] == "interrupted"
    with pytest.raises(InvalidInput, match="already attempted"):
        ledger.start(config, db, rid, policy)
    db.close()


def test_cancel_queued_document_marks_task_without_docker(config, policy, document_scope):
    rid = create(config, policy, document_scope)
    with patch.object(execution, "stop"), patch.object(document_worker, "stop_owned") as cleanup:
        execution.cancel(config, rid)
        cleanup.assert_not_called()
    db = database.connect(config.state_root)
    assert bindings.get(db, rid)["state"] == "interrupted"
    assert ledger.get_run(db, rid)["state"] == "cancelled"
    db.close()
