"""Capture-only candidate boundary, using real temporary journal transactions."""

import ast
import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from test_weixin_inbox import binding as binding  # shared isolated binding fixture
from test_weixin_inbox import message, poll, rows

from fiber_bundle import auth, database, weixin_hermes, weixin_receiver
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle.config import InvalidInput


class Adapter:
    _fiber_capture_version = 1

    def __init__(self):
        self._running = False
        self._poll_task = None
        self._account_id = "synthetic-bot"
        self._token = "synthetic-secret"
        self._base_url = "https://synthetic.invalid"
        self._poll_session = object()
        self.errors = []
        self.disconnected = 0

    def _set_fatal_error(self, code, detail, *, retryable):
        self.errors.append((code, detail, retryable))

    async def disconnect(self):
        assert self._poll_task is None
        self.disconnected += 1


def setup(config, binding):
    adapter = Adapter()
    weixin_receiver.configure(adapter, config, binding)
    adapter._running = True
    return adapter


def revoke(config, binding):
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=False, original=False, add=False)
    db.close()


def test_commit_precedes_next_fetch_and_restart_uses_journal(config, binding):
    adapter = setup(config, binding)
    observed = []

    async def fetch(session, **kwargs):
        observed.append(kwargs["sync_buf"])
        if len(observed) == 1:
            return poll(message(), cursor="one")
        assert len(rows(config, "events")) == 1
        adapter._running = False
        return poll(message("two"), cursor="two")

    asyncio.run(weixin_receiver.run(adapter, fetch))
    assert observed == ["", "one"]
    assert len(rows(config, "events")) == 2  # identical text is not content-deduplicated
    assert adapter.disconnected == 1 and not adapter.errors
    adapter = setup(config, binding)

    async def resumed(session, **kwargs):
        assert kwargs["sync_buf"] == "two"
        adapter._running = False
        return poll(cursor="three")

    asyncio.run(weixin_receiver.run(adapter, resumed))
    assert inbox.cursor(config, binding) == "three"


@pytest.mark.parametrize("mode", ["network", "invalid", "revoked", "disk"])
def test_failure_stops_without_retry_or_cursor_advance(config, binding, monkeypatch, mode):
    adapter = setup(config, binding)
    calls = []

    async def fetch(session, **kwargs):
        calls.append(1)
        if mode == "network":
            raise OSError("synthetic-secret body must not be logged")
        if mode == "revoked":
            revoke(config, binding)
        return {"ret": -1} if mode == "invalid" else poll(message())

    if mode == "disk":

        def fail(*args, **kwargs):
            raise OSError("synthetic-secret disk failure")

        monkeypatch.setattr(inbox, "receive", fail)
    asyncio.run(weixin_receiver.run(adapter, fetch))
    assert len(calls) == 1 and adapter.disconnected == 1
    assert rows(config, "bindings")[0]["cursor"] == ""
    assert not rows(config, "events")
    assert adapter.errors and "synthetic-secret" not in str(adapter.errors)
    assert adapter.errors[0][2] is False


def test_revoked_before_fetch_never_polls(config, binding):
    adapter = setup(config, binding)
    revoke(config, binding)

    async def fetch(*args, **kwargs):
        pytest.fail("revoked receiver must not poll")

    asyncio.run(weixin_receiver.run(adapter, fetch))
    assert adapter.errors[0][0] == "fiber_weixin_preflight_failed"


def test_cancellation_releases_receiver_lock_and_does_not_advance(config, binding):
    adapter = setup(config, binding)

    async def scenario():
        started = asyncio.Event()

        async def fetch(*args, **kwargs):
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(weixin_receiver.run(adapter, fetch))
        adapter._poll_task = task
        await started.wait()
        with pytest.raises(InvalidInput, match="already active"):
            with weixin_receiver.receiver_lock(config):
                pass
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with weixin_receiver.receiver_lock(config):
            pass

    asyncio.run(scenario())
    assert adapter.disconnected == 1 and not adapter.errors
    assert inbox.cursor(config, binding) == ""


def test_competing_receiver_never_fetches(config, binding):
    adapter = setup(config, binding)

    async def fetch(*args, **kwargs):
        pytest.fail("competing receiver must not poll")

    with weixin_receiver.receiver_lock(config):
        asyncio.run(weixin_receiver.run(adapter, fetch))
    assert adapter.errors[0][0] == "fiber_weixin_lock_failed"


def test_unknown_and_missing_media_are_retained_without_legacy_dispatch(config, binding):
    adapter = setup(config, binding)

    async def fetch(*args, **kwargs):
        adapter._running = False
        return poll(
            message("image", item_list=[{"type": 2}]),
            message("unknown", item_list=[{"type": 99}]),
        )

    asyncio.run(weixin_receiver.run(adapter, fetch))
    assert {r["state"] for r in rows(config, "events")} == {"incomplete", "unsupported"}
    assert inbox.cursor(config, binding) == "next"


def test_binding_cannot_change_or_be_configured_while_running(config, binding):
    adapter = setup(config, binding)
    with pytest.raises(InvalidInput, match="stopped"):
        weixin_receiver.configure(adapter, config, binding)
    adapter._account_id = "other-account"
    with pytest.raises(InvalidInput, match="mismatch"):
        weixin_receiver.preflight(adapter)


def test_candidate_pins_source_and_replaces_early_entry(monkeypatch):
    with pytest.raises(InvalidInput, match="source changed"):
        weixin_hermes.candidate(b"arbitrary source")
    # Small synthetic source exercises transformation; actual pinned source is
    # separately compiled and replayed in the candidate acceptance evidence.
    source = b"""_LIVE_ADAPTERS = {}
class WeixinAdapter:
    async def connect(self):
        try:
            if not self._acquire_platform_lock('weixin-bot-token', self._token, 'Weixin bot token'):
                return False
        except Exception as exc:
            logger.debug("[%s] Token lock unavailable (non-fatal): %s", self.name, exc)
        return True
    async def disconnect(self):
        _LIVE_ADAPTERS.pop(self._token, None)
    async def _poll_loop(self):
        raise AssertionError("legacy loop")
"""
    monkeypatch.setattr(weixin_hermes, "SOURCE_SHA256", hashlib.sha256(source).hexdigest())
    candidate = weixin_hermes.candidate(source)
    assert b"legacy loop" not in candidate
    tree = ast.parse(candidate)
    namespace = {}
    exec(compile(tree, "candidate", "exec"), namespace)
    cls = namespace["WeixinAdapter"]
    adapter = cls()
    adapter._set_fatal_error = lambda *a, **k: None
    assert asyncio.run(adapter.connect()) is False  # no trusted local setup
    monkeypatch.setattr(weixin_receiver, "preflight", lambda a: None)
    adapter._token = "synthetic"
    adapter._acquire_platform_lock = lambda *a: False
    assert asyncio.run(adapter.connect()) is False
    adapter._acquire_platform_lock = lambda *a: True
    assert asyncio.run(adapter.connect()) is True
    assert adapter._platform_lock_takeover_allowed is False
    namespace["_LIVE_ADAPTERS"]["synthetic"] = adapter
    duplicate = cls()
    duplicate._token = "synthetic"
    duplicate._set_fatal_error = lambda *args, **kwargs: None
    assert asyncio.run(duplicate.connect()) is False
    asyncio.run(duplicate.disconnect())
    assert namespace["_LIVE_ADAPTERS"]["synthetic"] is adapter
    seen = []

    async def capture(a, fetch):
        seen.append(a)

    monkeypatch.setattr(weixin_receiver, "run", capture)
    namespace["_get_updates"] = SimpleNamespace()
    asyncio.run(adapter._poll_loop())
    assert seen == [adapter]


def test_unpatched_adapter_cannot_be_configured(config, binding):
    adapter = Adapter()
    adapter._fiber_capture_version = None
    with pytest.raises(InvalidInput, match="candidate required"):
        weixin_receiver.configure(adapter, config, binding)


def test_silence_freezes_batch_while_next_poll_is_waiting(config, binding):
    from fiber_bundle import weixin_batches

    adapter = Adapter()
    weixin_receiver.configure(adapter, config, binding, quiet_seconds=0.02)
    adapter._running = True
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return poll(message())
        await asyncio.sleep(0.12)
        views = weixin_batches.list_batches(config, binding)["batches"]
        assert len(views) == 1 and views[0]["state"] == "frozen"
        adapter._running = False
        return poll(cursor="last")

    asyncio.run(weixin_receiver.run(adapter, fetch))
    assert not adapter.errors and len(calls) == 2


def test_batch_failure_after_capture_is_recoverable_without_refetch(config, binding, monkeypatch):
    from fiber_bundle import weixin_batches

    adapter = setup(config, binding)
    original = weixin_batches.advance

    def fail_after_receipt(*args, **kwargs):
        if rows(config, "events"):
            raise OSError("synthetic batch failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(weixin_batches, "advance", fail_after_receipt)

    async def fetch(*args, **kwargs):
        return poll(message())

    asyncio.run(weixin_receiver.run(adapter, fetch))
    assert adapter.errors[0][0] == "fiber_weixin_batch_failed"
    assert inbox.cursor(config, binding) == "next" and len(rows(config, "events")) == 1
    monkeypatch.setattr(weixin_batches, "advance", original)
    assert len(weixin_batches.advance(config, binding)[0]["members"]) == 1
