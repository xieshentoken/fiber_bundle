"""M4 publication from fixed Weixin batches; only local synthetic capture fixtures."""

import asyncio
import json
from unittest.mock import patch

import pytest
from test_article_tasks import call, fixture_worker
from test_articles import X_URL
from test_execution import policy as policy
from test_weixin_inbox import binding as binding
from test_weixin_inbox import rows
from test_weixin_task_inputs import CAPTURE, saved

from fiber_bundle import article_worker, auth, database, publication_files
from fiber_bundle import execution_ledger as ledger
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_publication as publication
from fiber_bundle import weixin_publication_dispatch as dispatch
from fiber_bundle import weixin_task_bindings as bindings
from fiber_bundle import weixin_task_delivery as delivery
from fiber_bundle import weixin_worker as worker
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.weixin_worker_config import WorkerConfig


def articles(config, binding, policy, *, count=1):
    bid, handoff = saved(config, binding, "save_separately", [X_URL] * count)
    runs = [
        bindings.submit(
            config,
            binding,
            bid,
            policy,
            capture_policy=CAPTURE,
            parent_receipt_id=r["job_id"],
        )["run_id"]
        for r in handoff["receipts"]
    ]
    return bid, runs


def complete(config, policy, rid):
    db = database.connect(config.state_root)
    ledger.start(config, db, rid, policy)
    db.close()
    with patch.object(article_worker, "execute_capture", side_effect=fixture_worker):
        call(config, policy, rid)
    db = database.connect(config.state_root)
    ledger.transition(db, rid, "succeeded")
    db.close()


def note(config, result):
    return config.project_root / "test vault/temp_vault1" / result["note_path"]


def test_disabled_without_journal_or_policy(config):
    assert dispatch.dispatch(config, None)["state"] == "disabled"
    assert not config.state_root.exists()


@pytest.mark.parametrize("value", [True, -1, 101, "1", 1.1])
def test_invalid_limits(config, value):
    with pytest.raises(InvalidInput):
        dispatch.dispatch(config, None, maximum=value)


def test_fixed_plan_awaits_capture_and_publication_before_delivery(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    plan = publication.plan(config, binding, bid)
    assert plan == publication.plan(config, binding, bid)
    assert dispatch.dispatch(config, binding, maximum=1)["state"] == "idle"
    complete(config, policy, runs[0])
    with pytest.raises(InvalidInput, match="publication not ready"):
        delivery.prepare(config, binding, bid)
    result = dispatch.dispatch(config, binding, maximum=1)
    assert result["processed"] == 1 and result["results"][0]["published"] is True
    published = result["results"][0]
    assert note(config, published).is_file()
    prepared = delivery.prepare(config, binding, bid)
    assert "文章已发布" in prepared["text"]
    assert published["source_version_id"] in prepared["text"]
    assert "发布需另行核验" not in prepared["text"]
    before = note(config, published).stat().st_ino
    assert dispatch.dispatch(config, binding, maximum=3)["processed"] == 0
    assert (
        publication.reconcile(config, binding, runs[0])["publication_id"]
        == published["publication_id"]
    )
    assert note(config, published).stat().st_ino == before
    sends = []

    async def sender(owner, text, guard):
        guard()
        sends.append(text)
        return True

    assert asyncio.run(delivery.deliver(config, binding, bid, sender))["state"] == "sent"
    assert asyncio.run(delivery.deliver(config, binding, bid, sender))["state"] == "sent"
    assert len(sends) == 1


def test_partial_batch_budget_deduplicates_note_keeps_two_originals(config, binding, policy):
    bid, runs = articles(config, binding, policy, count=2)
    publication.plan(config, binding, bid)
    for rid in runs:
        complete(config, policy, rid)
    first = dispatch.dispatch(config, binding, maximum=1)["results"][0]
    with pytest.raises(InvalidInput, match="publication not ready"):
        delivery.prepare(config, binding, bid)
    second = dispatch.dispatch(config, binding, maximum=1)["results"][0]
    assert first["publication_id"] == second["publication_id"]
    assert first["archive_receipt_id"] != second["archive_receipt_id"]
    # Byte-identical originals may share a Store version; both run receipts are retained.
    assert first["archive_version_id"] == second["archive_version_id"]
    assert delivery.prepare(config, binding, bid)["text"].count("文章已发布") == 2
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM article_publications").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM article_publication_runs").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("stage", ["before_plan", "before_publish", "before_send"])
@pytest.mark.parametrize("change", ["cancel", "add", "client"])
def test_cancellation_and_revocation_at_each_gate(config, binding, policy, stage, change):
    bid, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    if stage != "before_plan":
        publication.plan(config, binding, bid)
    if stage == "before_send":
        dispatch.dispatch(config, binding, maximum=1)
    if change == "cancel":
        batches.choose(config, binding, bid, "cancel")
    else:
        db = database.connect(config.state_root)
        if change == "add":
            auth.set_grant(
                db, binding.client_id, binding.vault_id, query=True, add=False, original=False
            )
        else:
            auth.revoke_client(db, binding.client_id)
        db.close()
    with pytest.raises((auth.Denied, InvalidInput)):
        if stage == "before_plan":
            publication.plan(config, binding, bid)
        elif stage == "before_publish":
            dispatch.dispatch(config, binding, maximum=1)
        else:
            delivery.prepare(config, binding, bid)
    assert len(list((config.project_root / "test vault/temp_vault1").glob("article-*"))) == (
        stage == "before_send"
    )


def test_crash_after_rename_blocks_automatic_retry_then_explicit_reconcile(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    rename = publication_files.rename_exclusive

    def crash(*args):
        rename(*args)
        raise SystemExit("synthetic process death")

    with patch.object(publication_files, "rename_exclusive", side_effect=crash):
        with pytest.raises(SystemExit):
            dispatch.dispatch(config, binding, maximum=1)
    assert rows(config, "wx_task_publications")[0]["phase"] == "claimed"
    target = next((config.project_root / "test vault/temp_vault1").glob("article-*/note.md"))
    before = (target.read_bytes(), target.stat().st_ino)
    with patch.object(
        publication_files, "rename_exclusive", side_effect=AssertionError("replayed")
    ):
        assert dispatch.dispatch(config, binding, maximum=5)["state"] == "blocked"
        assert publication.reconcile(config, binding, runs[0])["published"] is True
    assert before == (target.read_bytes(), target.stat().st_ino)
    assert "已发布" in delivery.prepare(config, binding, bid)["text"]


def test_failed_stage_requires_explicit_reconcile_preserves_partial_bytes(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    write = publication_files.write_new

    def full_disk(root, name, data):
        write(root, name, data[:8])
        raise OSError("synthetic failure")

    with patch.object(publication_files, "write_new", side_effect=full_disk):
        assert dispatch.dispatch(config, binding, maximum=1)["state"] == "stopped"
    partial = next((config.project_root / "test vault/temp_vault1").glob(".fiber-stage-*/note.md"))
    old = partial.read_bytes()
    assert dispatch.dispatch(config, binding, maximum=1)["state"] == "blocked"
    publication.reconcile(config, binding, runs[0])
    assert partial.read_bytes() == old and len(old) == 8


@pytest.mark.parametrize("change", ["edit", "delete", "image", "marker"])
def test_completed_files_changed_refuses_receipt_and_never_overwrites(
    config, binding, policy, change
):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    result = dispatch.dispatch(config, binding, maximum=1)["results"][0]
    path = note(config, result)
    if change == "delete":
        path.unlink()
    elif change == "edit":
        path.write_text("human revision")
    elif change == "image":
        next((path.parent / "images").iterdir()).write_bytes(b"human image")
    else:
        (path.parent / ".fiber-publication.json").write_text("{}")
    before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    with pytest.raises((IntegrityError, InvalidInput)):
        delivery.prepare(config, binding, bid)
    with pytest.raises((IntegrityError, InvalidInput)):
        publication.reconcile(config, binding, runs[0])
    assert before == {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}


def test_capture_receipt_cannot_be_replaced_by_publication(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    complete(config, policy, runs[0])
    assert "已发布" not in delivery.prepare(config, binding, bid)["text"]
    with pytest.raises(InvalidInput, match="frozen"):
        publication.plan(config, binding, bid)
    assert not list((config.project_root / "test vault/temp_vault1").glob("article-*"))


def test_publication_only_worker_needs_no_execution_policy(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    settings = WorkerConfig(binding, enabled=True, publication_maximum=1)
    with patch.object(article_worker, "execute_capture", side_effect=AssertionError("recapture")):
        result = worker.run(config, settings)
    assert result["processed"] == 0 and result["publication_processed"] == 1
    assert result["state"] == "limit_reached"


@pytest.mark.parametrize("value", [True, -1, 101, 1.5, "1"])
def test_worker_publication_limits_strict(config, binding, value):
    with pytest.raises(InvalidInput):
        worker.run(config, WorkerConfig(binding, publication_maximum=value))


def test_publication_config_backward_compatible_and_cli_no_policy_load(config, binding, policy):
    from dataclasses import asdict

    from test_weixin_worker import cli

    from fiber_bundle.weixin_worker_config import load_worker_config

    settings = WorkerConfig(binding, enabled=True, publication_maximum=1)
    path = config.project_root / "worker.json"
    data = asdict(settings)
    data.pop("publication_maximum")
    path.write_text(json.dumps(data))
    assert load_worker_config(config, path).publication_maximum == 0
    data["publication_maximum"] = 1
    path.write_text(json.dumps(data))
    bid, runs = articles(config, binding, policy)
    planned = cli(config, "publication-plan", "--batch-id", bid)
    assert planned.returncode == 0 and json.loads(planned.stdout)["run_ids"] == runs
    complete(config, policy, runs[0])
    result = cli(config, "run")
    assert result.returncode == 0 and json.loads(result.stdout)["publication_processed"] == 1
    assert not (config.project_root / settings.execution_config).exists()
    assert cli(config, "publication-reconcile", "--run-id", runs[0]).returncode == 0


def test_cross_account_cannot_reconcile_or_take_publication(config, binding, policy):
    from dataclasses import replace

    from fiber_bundle import weixin_inbox as inbox

    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    other = replace(binding, account_id="other-account")
    inbox.initialize(config, other)
    assert dispatch.dispatch(config, other, maximum=1)["processed"] == 0
    with pytest.raises((auth.Denied, InvalidInput)):
        publication.reconcile(config, other, runs[0])
    assert not list((config.project_root / "test vault/temp_vault1").glob("article-*"))


def test_partial_submission_refuses_plan(config, binding, policy):
    bid, handoff = saved(config, binding, "save_separately", [X_URL, X_URL])
    bindings.submit(
        config,
        binding,
        bid,
        policy,
        capture_policy=CAPTURE,
        parent_receipt_id=handoff["receipts"][0]["job_id"],
    )
    with pytest.raises(InvalidInput, match="incomplete"):
        publication.plan(config, binding, bid)


def test_missing_publication_run_refuses_scope_instead_of_capture_reply(config, binding, policy):
    from fiber_bundle import weixin_inbox as inbox

    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    with inbox.connection(config, binding) as db:
        db.execute("DELETE FROM wx_task_publications WHERE run_id=?", (runs[0],))
    with pytest.raises(IntegrityError, match="incomplete"):
        delivery.prepare(config, binding, bid)


def test_worker_execution_then_publication_uses_independent_budgets(config, binding, policy):
    from fiber_bundle import execution

    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)

    def executor(config, policy, rid, prompt):
        complete(config, policy, rid)
        return {"state": "succeeded"}

    settings = WorkerConfig(binding, enabled=True, maximum=1, publication_maximum=1)
    with patch.object(execution, "execute", side_effect=executor):
        result = worker.run(config, settings, policy)
    assert result["processed"] == result["publication_processed"] == 1
    assert "已发布" in delivery.prepare(config, binding, bid)["text"]
    assert rows(config, "wx_task_dispatches")[0]["phase"] == "finished"


def test_failed_publication_blocks_worker_without_recapture(config, binding, policy):
    bid, runs = articles(config, binding, policy)
    publication.plan(config, binding, bid)
    complete(config, policy, runs[0])
    settings = WorkerConfig(binding, enabled=True, publication_maximum=2, poll_limit=2)
    with patch.object(publication_files, "stage", side_effect=OSError("synthetic")):
        result = worker.run(config, settings)
    assert result["state"] == "stopped" and result["publication_processed"] == 1
    assert worker.run(config, settings)["state"] == "blocked"
