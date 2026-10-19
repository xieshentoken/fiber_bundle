import base64
import json
import sqlite3
import time
from unittest.mock import patch

import pytest
from test_article_capture import fixtures
from test_articles import X_URL
from test_execution import policy as policy

from fiber_bundle import article_bindings as bindings
from fiber_bundle import article_tasks, article_worker, auth, database, execution, model_gateway
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.article_capture import capture, verify_capture
from fiber_bundle.article_worker import CapturePolicy, CaptureResult
from fiber_bundle.articles import ArticleError
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.operations import intake, original, versions
from fiber_bundle.payloads import Intake

WORKER = CapturePolicy("sha256:" + "b" * 64)


@pytest.fixture
def article_scope(config, store, policy):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic-article-owner", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=False, original=False, add=True)
    data = Intake(
        vault_id=vid,
        event_id="article-event",
        source_key="article-url",
        session_id="synthetic",
        raw_event=base64.b64encode(b"synthetic event").decode(),
        assets=[
            {
                "role": "body",
                "media_type": "text/uri-list",
                "original_name": "url.txt",
                "data": base64.b64encode(X_URL.encode()).decode(),
            }
        ],
    )
    receipt = intake(config, db, client["id"], data)
    db.close()
    return client["id"], vid, receipt


def create(config, policy, scope):
    cid, vid, receipt = scope
    db = database.connect(config.state_root)
    try:
        return ledger.create_run(
            config,
            db,
            policy,
            cid,
            vid,
            receipt["source_version_id"],
            article=bindings.ArticleInput(receipt["job_id"], WORKER),
        )
    finally:
        db.close()


def start(config, policy, scope):
    rid = create(config, policy, scope)
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    db.close()
    return rid


def fixture_worker(config, policy, source_url, *, guard, task_id):
    guard()
    output = config.state_root / "article-captures" / task_id / "output"
    output.mkdir(parents=True)
    capture(source_url, output, fixtures)
    guard()
    return CaptureResult(task_id, output, verify_capture(output, source_url))


def call(config, policy, rid, body=None):
    return execution.handle(
        config,
        policy,
        rid,
        {
            "type": "article_capture",
            "id": "capture-call",
            "body": {} if body is None else body,
        },
    )


def test_add_only_can_capture_without_query_or_original(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    with patch.object(article_worker, "execute_capture", side_effect=fixture_worker):
        reply = call(config, policy, rid)
    assert reply["body"]["state"] == "captured" and reply["body"]["image_count"] == 1
    assert set(reply["body"]) == {"state", "image_count", "markdown_bytes", "published"}
    assert X_URL not in json.dumps(reply) and "/output" not in json.dumps(reply)
    db = database.connect(config.state_root)
    cid, vid, receipt = article_scope
    with pytest.raises(auth.Denied):
        versions(config, db, cid, vid, 0, 10)
    with pytest.raises(auth.Denied):
        original(config, db, cid, vid, receipt["source_version_id"], "unknown", 100)
    with pytest.raises(InvalidInput):
        execution.handle(config, policy, rid, {"type": "vaults", "id": "v", "body": {}})
    db.close()


@pytest.mark.parametrize(
    "query,original,add",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (False, False, True),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_permission_matrix(config, policy, article_scope, query, original, add):
    db = database.connect(config.state_root)
    auth.set_grant(db, article_scope[0], article_scope[1], query=query, original=original, add=add)
    db.close()
    if add:
        assert create(config, policy, article_scope)
    else:
        with pytest.raises(auth.Denied):
            create(config, policy, article_scope)


def test_another_client_even_with_full_grants_cannot_bind_receipt(config, policy, article_scope):
    db = database.connect(config.state_root)
    other = auth.create_client(db, "other-synthetic", False)
    auth.set_grant(db, other["id"], article_scope[1], query=True, original=True, add=True)
    db.close()
    with pytest.raises(auth.Denied):
        create(config, policy, (other["id"], *article_scope[1:]))
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM execution_runs").fetchone()[0] == 0
    db.close()


def test_other_vault_and_unstored_receipt_refused(config, policy, article_scope):
    cid, vid, receipt = article_scope
    db = database.connect(config.state_root)
    other = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, cid, other, query=True, original=True, add=True)
    db.close()
    with pytest.raises(auth.Denied):
        create(config, policy, (cid, other, receipt))
    db = database.connect(config.state_root)
    db.execute("UPDATE receipts SET state='incomplete' WHERE job_id=?", (receipt["job_id"],))
    db.close()
    with pytest.raises(auth.Denied):
        create(config, policy, article_scope)


@pytest.mark.parametrize(
    "body", [{"url": "https://example.com/other"}, {"vault_id": "other"}, {"output": "/private"}]
)
def test_tool_rejects_scope_overrides_before_worker(config, policy, article_scope, body):
    rid = start(config, policy, article_scope)
    with patch.object(article_worker, "execute_capture") as worker:
        with pytest.raises(InvalidInput):
            call(config, policy, rid, body)
        worker.assert_not_called()


@pytest.mark.parametrize("kind", ["client", "add"])
def test_revocation_denies_capture_and_model_without_egress(config, policy, article_scope, kind):
    rid = start(config, policy, article_scope)
    db = database.connect(config.state_root)
    if kind == "client":
        auth.revoke_client(db, article_scope[0])
    else:
        auth.set_grant(db, article_scope[0], article_scope[1], query=True, original=True, add=False)
    db.close()
    with (
        patch.object(article_worker, "execute_capture") as worker,
        patch.object(model_gateway, "complete") as provider,
    ):
        with pytest.raises(auth.Denied):
            call(config, policy, rid)
        with pytest.raises(auth.Denied):
            execution.handle(config, policy, rid, {"type": "model", "id": "m", "body": {}})
        worker.assert_not_called()
        provider.assert_not_called()


def test_model_request_rebuilt_without_pi_supplied_data(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    response = {
        "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    with patch.object(model_gateway, "complete", return_value=response) as provider:
        execution.handle(
            config,
            policy,
            rid,
            {
                "type": "model",
                "id": "m",
                "body": {
                    "messages": [{"role": "user", "content": "PRIVATE_ARTICLE_SENTINEL " + X_URL}],
                    "tools": [{"description": "PRIVATE_TOOL_SENTINEL"}],
                },
            },
        )
    outbound = provider.call_args.args[2]
    assert outbound["messages"] == [{"role": "user", "content": bindings.ARTICLE_PROMPT}]
    assert outbound["tools"] == [bindings.TOOL]
    assert "PRIVATE" not in json.dumps(outbound) and X_URL not in json.dumps(outbound)


def test_duplicate_tool_call_returns_reverified_counts_without_refetch(
    config, policy, article_scope
):
    rid = start(config, policy, article_scope)
    with patch.object(article_worker, "execute_capture", side_effect=fixture_worker) as worker:
        first = call(config, policy, rid)
        assert call(config, policy, rid) == first
        assert worker.call_count == 1
    db = database.connect(config.state_root)
    task = bindings.get(db, rid)
    output = article_tasks.output_path(config, task)
    db.close()
    (output / "capture.json").write_text("{}")
    with pytest.raises(InvalidInput, match="manifest changed"):
        call(config, policy, rid)


def test_revoke_during_capture_discards_delivery_and_preserves_files(config, policy, article_scope):
    rid = start(config, policy, article_scope)

    def revoked(*args, **kwargs):
        result = fixture_worker(*args, **kwargs)
        db = database.connect(config.state_root)
        auth.revoke_client(db, article_scope[0])
        db.close()
        return result

    with patch.object(article_worker, "execute_capture", side_effect=revoked):
        with pytest.raises(auth.Denied):
            call(config, policy, rid)
    db = database.connect(config.state_root)
    task = bindings.get(db, rid)
    assert task["state"] == "failed"
    assert (article_tasks.output_path(config, task) / "source.html").is_file()
    db.close()


def test_failed_capture_is_not_automatically_replayed(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    with patch.object(
        article_worker, "execute_capture", side_effect=ArticleError("synthetic")
    ) as worker:
        with pytest.raises(ArticleError):
            call(config, policy, rid)
        with pytest.raises(InvalidInput):
            call(config, policy, rid)
        assert worker.call_count == 1


def test_cancel_targets_bound_capture_and_is_terminal(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    db = database.connect(config.state_root)
    db.execute("UPDATE article_tasks SET state='capturing' WHERE run_id=?", (rid,))
    task = bindings.get(db, rid)
    db.close()
    with patch.object(execution, "stop"), patch.object(article_worker, "remove_container") as stop:
        execution.cancel(config, rid)
        stop.assert_called_once_with("fiber-capture-" + task["capture_task_id"])
    with pytest.raises(InvalidInput):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    assert ledger.get_run(db, rid)["state"] == "cancelled"
    assert bindings.get(db, rid)["state"] == "interrupted"
    db.close()


def test_original_url_bytes_revalidated(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    db = database.connect(config.state_root)
    asset = db.execute(
        "SELECT a.* FROM assets a JOIN version_assets va ON va.asset_id=a.id "
        "WHERE va.version_id=? AND va.role='body'",
        (article_scope[2]["source_version_id"],),
    ).fetchone()
    db.close()
    (config.state_root / asset["relative_path"]).write_bytes(b"changed")
    with pytest.raises(IntegrityError):
        call(config, policy, rid)


def test_expired_execution_deadline_does_not_launch(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    with patch.object(article_worker, "execute_capture") as worker:
        with pytest.raises(InvalidInput):
            article_tasks.capture_bound(config, policy, rid, {}, deadline=time.monotonic() - 1)
        worker.assert_not_called()


def test_read_execution_cannot_invoke_article_tool(config, policy, article_scope):
    cid, vid, receipt = article_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    rid = ledger.create_run(config, db, policy, cid, vid, receipt["source_version_id"])
    ledger.start(config, db, rid, policy)
    db.close()
    with patch.object(article_worker, "execute_capture") as worker:
        with pytest.raises(InvalidInput, match="not an article"):
            call(config, policy, rid)
        worker.assert_not_called()


def test_cleanup_failure_can_be_retried(config, policy, article_scope):
    rid = start(config, policy, article_scope)
    db = database.connect(config.state_root)
    db.execute("UPDATE article_tasks SET state='capturing' WHERE run_id=?", (rid,))
    db.close()
    with patch.object(
        article_worker, "remove_container", side_effect=OSError("daemon unavailable")
    ):
        with pytest.raises(OSError):
            article_tasks.stop(config, rid)
    with patch.object(article_worker, "remove_container") as cleanup:
        article_tasks.stop(config, rid)
        cleanup.assert_called_once()


def test_v4_migration_failure_rolls_back_v3(config):
    config.state_root.mkdir()
    path = config.state_root / "metadata.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(database.SCHEMA + database.SCHEMA_V2 + database.SCHEMA_V3)
    db.execute("PRAGMA user_version=3")
    db.close()
    with patch.object(database, "SCHEMA_V4", database.SCHEMA_V4 + "INVALID SQL;"):
        with pytest.raises(sqlite3.Error):
            database.migrate(config)
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 13
    db.close()
    assert len(list((config.state_root / "snapshots").glob("*.sqlite3"))) == 1


def test_schema3_upgrade_has_backup_and_preserves_old_rows(config):
    config.state_root.mkdir()
    path = config.state_root / "metadata.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(database.SCHEMA + database.SCHEMA_V2 + database.SCHEMA_V3)
    db.execute("INSERT INTO vaults(id,name,relative_path) VALUES('v','fixture','fixture')")
    db.execute("PRAGMA user_version=3")
    db.commit()
    db.close()
    with pytest.raises(IntegrityError):
        database.connect(config.state_root)
    backup = database.migrate(config)
    assert sqlite3.connect(backup).execute("PRAGMA user_version").fetchone()[0] == 3
    db = database.connect(config.state_root)
    assert db.execute("SELECT name FROM vaults").fetchone()[0] == "fixture"
    assert db.execute("SELECT count(*) FROM article_tasks").fetchone()[0] == 0
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()
