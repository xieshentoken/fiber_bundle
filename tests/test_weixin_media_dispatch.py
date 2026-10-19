"""Frozen-scope discovery and receiver media budgets with real isolated journals."""

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows
from test_weixin_receiver import Adapter, revoke

from fiber_bundle import database
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_download as download
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_media_dispatch as dispatch
from fiber_bundle import weixin_media_queue as queue
from fiber_bundle import weixin_receiver as receiver
from fiber_bundle import weixin_recovery as recovery
from fiber_bundle import weixin_store as handoff
from fiber_bundle.config import IntegrityError, InvalidInput


def media(kind=4, path="file", **extra):
    name = {2: "image_item", 3: "voice_item", 4: "file_item", 5: "video_item"}[kind]
    return {
        "type": kind,
        name: {"media": {"full_url": f"https://novac2c.cdn.weixin.qq.com/{path}"}},
        **extra,
    }


def capture(config, binding, *messages):
    cursor = inbox.cursor(config, binding)
    result = inbox.receive(config, binding, cursor, poll(*messages, cursor=cursor + "x"))
    confirmations.initialize(config, binding)
    confirmations.process(config, binding)
    return result["events"]


def freeze(config, binding):
    return batches.advance(config, binding, now=datetime.now(UTC) + timedelta(seconds=5))[0]


async def good(url):
    return b"synthetic original bytes"


def drain(config, binding, **kwargs):
    return asyncio.run(dispatch.drain(config, binding, fetcher=good, **kwargs))


@pytest.fixture
def frozen(config, binding):
    events = capture(config, binding, message(item_list=[media(path="one"), media(path="two")]))
    view = freeze(config, binding)
    confirmations.prepare(config, binding, view["batch_id"])
    return events[0]["job_id"], view["batch_id"]


@pytest.mark.parametrize("value", [-1, 101, True, 1.5, "1", None])
def test_invalid_budget(config, binding, value):
    with pytest.raises(InvalidInput):
        drain(config, binding, maximum=value)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_invalid_cursor(config, binding, value):
    with pytest.raises(InvalidInput):
        drain(config, binding, after=value)


def test_default_disabled_no_queue_or_network(config, binding, frozen):
    assert drain(config, binding)["processed"] == 0
    with inbox.connection(config, binding) as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='wx_download_attempts'"
        ).fetchone()


def test_bounded_partial_completion_replay_and_no_overwrite(config, binding, frozen):
    original = rows(config, "events")[0]
    with pytest.raises(InvalidInput, match="incomplete"):
        batches.choose(config, binding, frozen[1], "save_one")
    first = drain(config, binding, maximum=1)
    assert first["processed"] == 1 and first["state"] == "limit_reached"
    assert not batches.get(config, binding, frozen[1])["content_ready"]
    assert len(rows(config, "wx_download_attempts")) == 1
    second = drain(config, binding, maximum=1, after=first["next_cursor"])
    assert second["processed"] == 1
    assert batches.get(config, binding, frozen[1])["content_ready"]
    assert drain(config, binding, maximum=10)["processed"] == 0
    assert len(rows(config, "wx_download_attempts")) == 2
    assert rows(config, "events")[0] == original
    batches.choose(config, binding, frozen[1], "save_one")
    assert handoff.transfer(config, binding, frozen[1])["store_state"] == "stored"
    db = database.connect(config.state_root)
    assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_buffering_unclassified_cancelled_and_other_account_not_discovered(config, binding):
    capture(config, binding, message(item_list=[media()]))
    batches.advance(config, binding)  # buffering
    assert drain(config, binding, maximum=10)["processed"] == 0
    view = freeze(config, binding)
    batches.choose(config, binding, view["batch_id"], "cancel")
    assert drain(config, binding, maximum=10)["processed"] == 0
    with pytest.raises(InvalidInput, match="binding"):
        drain(config, replace(binding, account_id="other"), maximum=10)
    assert rows(config, "wx_download_attempts") == []


def test_manual_queued_job_outside_frozen_scope_is_not_executed(config, binding):
    queue.initialize(config, binding)
    job = inbox.receive(config, binding, "", poll(message(item_list=[media()])))["events"][0][
        "job_id"
    ]
    attempt = queue.enqueue(config, binding, job)[0]
    confirmations.initialize(config, binding)
    assert drain(config, binding, maximum=10)["processed"] == 0
    assert queue.status(config, binding, attempt["id"])["state"] == "queued"


def test_nested_original_media_and_supported_kinds(config, binding):
    events = capture(
        config,
        binding,
        message(
            item_list=[media(kind=k, path=str(k)) for k in (2, 3, 4, 5)]
            + [
                {
                    "type": 1,
                    "text_item": {"text": "quoted original"},
                    "ref_msg": {"message_item": media(path="ref")},
                }
            ]
        ),
    )
    freeze(config, binding)
    result = drain(config, binding, maximum=10)
    assert result["processed"] == 5
    assert recovery.view(config, binding, events[0]["job_id"])["missing_items"] == []
    assert {r["item_key"] for r in rows(config, "wx_recovered_media")} == {
        "0",
        "1",
        "2",
        "3",
        "4.ref",
    }


def test_unsupported_batch_not_partially_downloaded(config, binding):
    capture(
        config, binding, message(item_list=[media()]), message("unknown", item_list=[{"type": 9}])
    )
    freeze(config, binding)
    assert drain(config, binding, maximum=10)["processed"] == 0
    assert rows(config, "wx_download_attempts") == []


@pytest.mark.parametrize("failure", ["expired", "malformed", "transport"])
def test_failure_not_retried_on_restart_and_explicit_retry_keeps_history(
    config, binding, frozen, failure
):
    calls = []

    async def failed(url):
        calls.append(url)
        if failure == "expired":
            raise download.DownloadError("media_http_rejected")
        if failure == "malformed":
            return b""
        raise OSError("private URL or key must not enter status")

    result = asyncio.run(dispatch.drain(config, binding, maximum=2, fetcher=failed))
    assert result["state"] == "stopped" and result["processed"] == 1 and len(calls) == 1
    failed_attempt = result["attempts"][0]
    assert failed_attempt["state"] == "failed"
    restarted = drain(config, binding, maximum=2)
    assert restarted["state"] == "recovery_required" and restarted["processed"] == 0
    assert not rows(config, "wx_recovered_media")
    retried = queue.retry(config, binding, failed_attempt["id"])
    assert retried["attempt"] == 2
    assert drain(config, binding, maximum=2)["processed"] == 2
    assert queue.status(config, binding, failed_attempt["id"]) == failed_attempt
    assert len(rows(config, "wx_download_attempts")) == 3


def test_descriptor_rejection_before_fetch(config, binding):
    capture(
        config,
        binding,
        message(
            item_list=[
                {"type": 4, "file_item": {"media": {"full_url": "http://localhost/private"}}}
            ]
        ),
    )
    freeze(config, binding)

    async def forbidden(url):
        pytest.fail("unsafe source cannot reach network")

    result = asyncio.run(dispatch.drain(config, binding, maximum=1, fetcher=forbidden))
    assert result["state"] == "stopped"


def test_claim_crash_blocks_then_reconciles_without_redownload(config, binding, frozen):
    queue.initialize(config, binding)
    attempt = queue.enqueue(config, binding, frozen[0], maximum=1)[0]
    queue.claim(config, binding, attempt_id=attempt["id"])
    blocked = drain(config, binding, maximum=1)
    assert blocked["state"] == "recovery_required" and blocked["processed"] == 0
    assert blocked["attempts"][0]["state"] == "running"
    sha = recovery.view(config, binding, frozen[0])["raw_sha256"]
    recovery.complete(
        config, binding, frozen[0], sha, {attempt["item_key"]: b"committed before crash"}
    )
    assert queue.reconcile(config, binding, attempt["id"])["state"] == "succeeded"
    assert drain(config, binding, maximum=1)["processed"] == 1
    assert len(rows(config, "wx_download_attempts")) == 2


def test_cancel_during_fetch_leaves_bytes_uncommitted(config, binding, frozen):
    async def cancel(url):
        batches.choose(config, binding, frozen[1], "cancel")
        return b"cancelled bytes"

    result = asyncio.run(dispatch.drain(config, binding, maximum=2, fetcher=cancel))
    assert result["state"] == "stopped" and result["processed"] == 1
    assert rows(config, "wx_recovered_media") == []
    assert drain(config, binding, maximum=2)["processed"] == 0


def test_revoke_during_fetch_stops_and_preserves_running(config, binding, frozen):
    async def denied(url):
        revoke(config, binding)
        return b"revoked bytes"

    result = asyncio.run(dispatch.drain(config, binding, maximum=2, fetcher=denied))
    assert result["state"] == "blocked" and result["processed"] == 1
    assert rows(config, "wx_recovered_media") == []
    assert rows(config, "wx_download_attempts")[0]["state"] == "running"


def test_scope_change_during_fetch_rejected(config, binding, frozen):
    async def corrupt(url):
        with inbox.connection(config, binding) as db:
            db.execute("UPDATE wx_confirmation_prompts SET members_json='[]'")
        return b"wrong scope"

    result = asyncio.run(dispatch.drain(config, binding, maximum=1, fetcher=corrupt))
    assert result["state"] == "stopped"
    assert rows(config, "wx_recovered_media") == []


def test_corrupt_raw_fails_before_attempt_or_network(config, binding, frozen):
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE events SET raw=x'00'")
    with pytest.raises(IntegrityError):
        drain(config, binding, maximum=1)
    assert rows(config, "wx_download_attempts") == []


def test_worker_conflict_never_fetches(config, binding, frozen):
    with queue.worker_lock(config), pytest.raises(InvalidInput, match="active"):
        drain(config, binding, maximum=1)
    assert rows(config, "wx_recovered_media") == []


def test_discovery_pages_do_not_starve_new_events(config, binding):
    # Prior incomplete deliveries have since been recovered, without rewriting events.
    first = capture(config, binding, *(message(str(i), item_list=[media()]) for i in range(100)))
    freeze(config, binding)
    queue.initialize(config, binding)
    for item in first:
        sha = recovery.view(config, binding, item["job_id"])["raw_sha256"]
        recovery.complete(config, binding, item["job_id"], sha, {"0": b"prior original"})
    second = capture(config, binding, message("new", item_list=[media()]))
    freeze(config, binding)
    page = drain(config, binding, maximum=1)
    assert page["state"] == "page_complete" and page["processed"] == 0
    next_page = drain(config, binding, maximum=1, after=page["next_cursor"])
    assert next_page["processed"] == 1
    assert next_page["attempts"][0]["job_id"] == second[0]["job_id"]


def test_coroutine_cancel_releases_lock_and_requires_explicit_retry(config, binding, frozen):
    async def scenario():
        entered = asyncio.Event()

        async def hold(url):
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(dispatch.drain(config, binding, maximum=2, fetcher=hold))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    with queue.worker_lock(config):
        pass
    assert rows(config, "wx_download_attempts")[0]["state"] == "interrupted"
    assert drain(config, binding, maximum=1)["state"] == "recovery_required"
    assert rows(config, "wx_recovered_media") == []


def test_receiver_default_off_and_invalid_budget(config, binding, frozen):
    adapter = Adapter()
    with pytest.raises(InvalidInput):
        receiver.configure(adapter, config, binding, media_limit=True)
    receiver.configure(adapter, config, binding)
    assert adapter._fiber_media_limit == 0
    adapter._running = True

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(cursor="default-off")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors
    with inbox.connection(config, binding) as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='wx_download_attempts'"
        ).fetchone()


def test_receiver_total_budget_between_polls_then_restart(config, binding, frozen, monkeypatch):
    real = dispatch.drain
    calls = []

    async def bounded(config, binding, **kwargs):
        calls.append(inbox.cursor(config, binding))
        return await real(config, binding, fetcher=good, **kwargs)

    monkeypatch.setattr(dispatch, "drain", bounded)
    for run in range(2):
        adapter = Adapter()
        receiver.configure(adapter, config, binding, media_limit=1)
        adapter._running = True
        polls = []

        async def fetch(*args, adapter=adapter, run=run, polls=polls, **kwargs):
            polls.append(1)
            if len(polls) == 3:
                adapter._running = False
            return poll(cursor=f"run-{run}-page-{len(polls)}")

        asyncio.run(receiver.run(adapter, fetch))
        assert not adapter.errors and len(polls) == 3
        assert len(calls) == run + 1
    assert batches.get(config, binding, frozen[1])["content_ready"]
    assert len(rows(config, "wx_download_attempts")) == 2


def test_returned_poll_commits_before_failed_media(config, binding, monkeypatch):
    adapter = Adapter()
    real = dispatch.drain
    clock = batches.timestamp
    time = [clock(None)]
    monkeypatch.setattr(batches, "timestamp", lambda now: time[0])

    async def bad(url):
        assert inbox.cursor(config, binding) == "captured"
        assert len(rows(config, "events")) == 1
        raise OSError("private transport data")

    async def bounded(config, binding, **kwargs):
        return await real(config, binding, fetcher=bad, **kwargs)

    monkeypatch.setattr(dispatch, "drain", bounded)
    receiver.configure(adapter, config, binding, media_limit=2)
    adapter._running = True
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return poll(message(item_list=[media()]), cursor="captured")
        time[0] += 5
        return poll(cursor="captured")

    asyncio.run(receiver.run(adapter, fetch))
    assert len(calls) == 2
    assert adapter.errors[0][0] == "fiber_weixin_media_failed"
    assert "private" not in str(adapter.errors)
    assert inbox.cursor(config, binding) == "captured"
    assert rows(config, "wx_download_attempts")[0]["state"] == "failed"


def test_receiver_media_then_confirmed_store_handoff(config, binding, frozen, monkeypatch):
    real = dispatch.drain
    adapter = Adapter()

    async def bounded(config, binding, **kwargs):
        return await real(config, binding, fetcher=good, **kwargs)

    monkeypatch.setattr(dispatch, "drain", bounded)
    receiver.configure(adapter, config, binding, media_limit=2, store_limit=1, intent_routing=True)
    adapter._running = True
    fetched = []

    async def fetch(*args, **kwargs):
        fetched.append(1)
        if len(fetched) == 1:
            assert not batches.get(config, binding, frozen[1])["content_ready"]
            return poll(cursor="first-part")
        if len(fetched) == 2:
            assert batches.get(config, binding, frozen[1])["content_ready"]
            # An existing prompt is never taken over by automatic source selection.
            assert batches.get(config, binding, frozen[1])["state"] == "frozen"
            prompt = confirmations.list_prompts(config, binding)["prompts"][0]
            return poll(
                message(
                    "choice",
                    item_list=[
                        {"type": 1, "text_item": {"text": f"批次 {prompt['prompt_id']} 保存为一份"}}
                    ],
                ),
                cursor="choice",
            )
        db = database.connect(config.state_root)
        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
        db.close()
        adapter._running = False
        return poll(cursor="stored")

    asyncio.run(receiver.run(adapter, fetch))
    assert not adapter.errors and len(fetched) == 3
    assert len(rows(config, "wx_batch_members")) == 1
    assert len(rows(config, "wx_recovered_media")) == 2


def test_explicit_worker_attempt_does_not_claim_older_unrelated_job(config, binding):
    from fiber_bundle import weixin_media_worker as worker

    queue.initialize(config, binding)
    jobs = capture(
        config, binding, message(item_list=[media()]), message("second", item_list=[media()])
    )
    first = queue.enqueue(config, binding, jobs[0]["job_id"])[0]
    second = queue.enqueue(config, binding, jobs[1]["job_id"])[0]
    result = asyncio.run(worker.dispatch(config, binding, attempt_id=second["id"], fetcher=good))
    assert result["attempts"][0]["id"] == second["id"]
    assert queue.status(config, binding, first["id"])["state"] == "queued"
    assert len(rows(config, "wx_recovered_media")) == 1


@pytest.mark.parametrize("value", [0, True, 101, -1, 1.5])
def test_enqueue_limit_rejects_before_mutation(config, binding, frozen, value):
    queue.initialize(config, binding)
    with pytest.raises(InvalidInput):
        queue.enqueue(config, binding, frozen[0], maximum=value)
    assert rows(config, "wx_download_attempts") == []


def test_cancelled_stale_queue_does_not_block_active_batch(config, binding):
    queue.initialize(config, binding)
    old = capture(config, binding, message("old", item_list=[media()]))[0]["job_id"]
    old_batch = freeze(config, binding)
    attempt = queue.enqueue(config, binding, old)[0]
    batches.choose(config, binding, old_batch["batch_id"], "cancel")
    new = capture(config, binding, message("new", item_list=[media()]))[0]["job_id"]
    freeze(config, binding)
    result = drain(config, binding, maximum=1)
    assert result["attempts"][0]["job_id"] == new
    assert queue.status(config, binding, attempt["id"])["state"] == "queued"


def test_corrupt_attempt_identity_refused_before_fetch(config, binding, frozen):
    queue.initialize(config, binding)
    attempt = queue.enqueue(config, binding, frozen[0], maximum=1)[0]
    with inbox.connection(config, binding) as db:
        db.execute(
            "UPDATE wx_download_attempts SET raw_sha256='wrong' WHERE id=?", (attempt["id"],)
        )

    async def forbidden(url):
        pytest.fail("changed attempt identity must not download")

    result = asyncio.run(dispatch.drain(config, binding, maximum=1, fetcher=forbidden))
    assert result["state"] == "stopped"
    assert rows(config, "wx_recovered_media") == []


def test_concurrent_discovery_downloads_original_once(config, binding, frozen):
    calls = []

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held(url):
            calls.append(url)
            entered.set()
            await release.wait()
            return b"once"

        first = asyncio.create_task(dispatch.drain(config, binding, maximum=1, fetcher=held))
        await entered.wait()
        competing = await dispatch.drain(config, binding, maximum=1, fetcher=held)
        assert competing["state"] == "recovery_required"
        assert competing["processed"] == 0
        release.set()
        result = await first
        assert result["attempts"][0]["state"] == "succeeded"

    asyncio.run(scenario())
    assert len(calls) == 1
    assert len(rows(config, "wx_download_attempts")) == 1
    assert len(rows(config, "wx_recovered_media")) == 1


def test_revoked_before_discovery_does_not_enqueue(config, binding, frozen):
    from fiber_bundle import auth

    revoke(config, binding)
    with pytest.raises(auth.Denied):
        drain(config, binding, maximum=1)
    assert rows(config, "events")
    with sqlite3.connect(config.state_root / "weixin-inbox.sqlite3") as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='wx_download_attempts'"
        ).fetchone()


def test_frozen_members_unchanged_when_new_message_arrives_during_download(config, binding, frozen):
    async def later(url):
        capture(config, binding, message("later", item_list=[media(path="later")]))
        return b"original"

    result = asyncio.run(dispatch.drain(config, binding, maximum=1, fetcher=later))
    assert result["attempts"][0]["state"] == "succeeded"
    assert len(batches.get(config, binding, frozen[1])["members"]) == 1
    assert len(rows(config, "events")) == 2
    assert len(rows(config, "wx_recovered_media")) == 1
    assert rows(config, "wx_recovered_media")[0]["event_id"] == "one"
