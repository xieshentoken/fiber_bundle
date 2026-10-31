"""Idle observation is read-only; work hints cannot authorize or duplicate a claim."""

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from test_channel_intake import enable
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import auth, database
from fiber_bundle.channels import (
    dispatch,
    idle,
    intake_extraction,
    intake_processing,
    intake_receipts,
    intake_repository,
    model_answer,
    query,
)
from fiber_bundle.channels import store as channels
from fiber_bundle.channels.services import policy
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake"]


def test_regular_channel_connection_does_not_reserve_a_writer(config, store):
    with channels.opened(config):
        pass
    path = config.state_root / "message-channels.sqlite3"
    before = path.stat().st_mtime_ns
    with sqlite3.connect(path, isolation_level=None) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            with channels.opened(config) as reader:
                assert reader.execute("PRAGMA synchronous").fetchone()[0] == 3
                assert reader.execute("PRAGMA fullfsync").fetchone()[0] == 1
                assert reader.execute("SELECT count(*) FROM channels").fetchone()[0] == 0
        finally:
            writer.execute("ROLLBACK")
    assert path.stat().st_mtime_ns == before


def test_version_three_additive_schema_is_still_initialized(config, store):
    with channels.opened(config) as db:
        for table in (
            "channel_intake_policies",
            "channel_intake_records",
            "channel_intake_consumption",
        ):
            db.execute(f"DROP TABLE {table}")
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    with channels.opened(config) as db:
        present = {tuple(row) for row in db.execute("SELECT type,name FROM sqlite_master")}
        assert channels.SCHEMA_OBJECTS <= present


def test_idle_hint_defers_additive_migration_to_locked_connection(config, channel):
    value, _ = channel
    bind(config, value)
    with channels.opened(config) as db:
        db.execute("DROP TABLE channel_intake_consumption")
    assert idle.query_pending(config, value["id"], 1, independent=False)
    with channels.readonly(config) as db:
        assert not channels.schema_ready(db)
    dispatch.advance(config, value["id"], 1)
    with channels.readonly(config) as db:
        assert channels.schema_ready(db)


def test_idle_hints_do_not_create_missing_sidecar(config, store):
    identity = str(uuid4())
    path = config.state_root / "message-channels.sqlite3"
    assert not path.exists()
    assert not idle.query_pending(config, identity, 1, independent=False)
    assert not idle.delivery_pending(config, identity, 1)
    dispatch.advance(config, identity, 1)
    model_answer.advance(config, identity, 1)
    assert dispatch.prepare_delivery(config, identity, 1) == "idle"
    assert not path.exists()
    assert not (config.state_root / "execution-locks").exists()


def test_idle_query_ticks_need_no_storage_lock_or_database_write(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    path = config.state_root / "message-channels.sqlite3"
    before = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns

    def forbidden(*args):
        pytest.fail("idle tick acquired the global storage lock")

    monkeypatch.setattr(database, "locked", forbidden)
    started = time.monotonic()
    for _ in range(100):
        dispatch.advance(config, value["id"], 1)
        model_answer.advance(config, value["id"], 1)
        assert dispatch.prepare_delivery(config, value["id"], 1) == "idle"
    print(f"300 idle query/send ticks: {time.monotonic() - started:.3f}s; storage locks=0")
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before


def test_idle_intake_ticks_need_no_storage_lock(config, entry, monkeypatch):
    enable(config, entry, fetch_articles=True, receipt=True)

    def forbidden(*args):
        pytest.fail("idle intake acquired the global storage lock")

    monkeypatch.setattr(database, "locked", forbidden)
    for _ in range(20):
        assert intake_extraction.advance(config, entry[0]["id"]) is False
        assert intake_receipts.take(config, entry[0]["id"]) is None


@pytest.mark.parametrize("receipt", [False, True])
def test_intake_hint_allows_missing_additive_table_migration(config, entry, receipt):
    enable(config, entry, fetch_articles=True, receipt=True)
    with channels.opened(config) as db:
        db.execute("DROP TABLE channel_intake_records")
    claim = intake_receipts.take if receipt else intake_extraction.advance
    assert claim(config, entry[0]["id"]) is (None if receipt else False)
    with channels.readonly(config) as db:
        assert channels.schema_ready(db)


def _before_claim(monkeypatch, action):
    original = database.locked
    pending = True

    @contextmanager
    def changed(config):
        nonlocal pending
        if pending:
            pending = False
            action()
        with original(config):
            yield

    monkeypatch.setattr(database, "locked", changed)


@pytest.mark.parametrize("delivery", [False, True])
@pytest.mark.parametrize("change", ["pause", "revision", "revoke", "service", "watermark"])
def test_claim_rechecks_state_permissions_intent_and_watermark(
    config, channel, monkeypatch, delivery, change
):
    value, _ = channel
    binding = bind(config, value)
    if change in {"service", "watermark"}:
        policy.change(config, value["id"], "query", 0, enabled=True)
    assert dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询：压力")
    if delivery:
        dispatch.advance(config, value["id"], 1, independent=True)
    expected_state = "ready" if delivery else "captured"

    def changed():
        if change == "revoke":
            with database.connect(config.state_root) as main:
                main.execute(
                    "UPDATE client_vault_grants SET can_query=0 WHERE client_id=?",
                    (binding["value"]["client_id"],),
                )
        elif change == "service":
            policy.change(config, value["id"], "query", 1, enabled=False)
        else:
            with channels.opened(config) as db:
                if change == "pause":
                    db.execute("UPDATE channels SET state='paused' WHERE id=?", (value["id"],))
                elif change == "revision":
                    db.execute("UPDATE channels SET revision=2 WHERE id=?", (value["id"],))
                else:
                    service = policy.get(config, value["id"], "query")
                    service["admit_after"] = time.time() + 60
                    db.execute(
                        "UPDATE channel_services SET value=? WHERE channel_id=? AND role='query'",
                        (json.dumps(service), value["id"]),
                    )

    _before_claim(monkeypatch, changed)
    monkeypatch.setattr(query, "submit", lambda *args: pytest.fail("withdrawn work executed"))

    def claim():
        if delivery:
            return dispatch.prepare_delivery(config, value["id"], 1)
        return dispatch.advance(config, value["id"], 1, independent=True)

    if change in {"service", "watermark"}:
        assert claim() == ("idle" if delivery else None)
    else:
        with pytest.raises((InvalidInput, auth.Denied)):
            claim()
    with channels.readonly(config) as db:
        assert db.execute("SELECT state FROM events WHERE event_id='one'").fetchone()[0] == (
            expected_state
        )


def test_two_positive_hints_claim_query_only_once(config, channel, monkeypatch):
    value, _ = channel
    bind(config, value)
    assert dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询：压力")
    barrier = Barrier(2)
    original_hint, original_submit = idle.query_pending, query.submit
    calls = []

    def hint(*args, **kwargs):
        pending = original_hint(*args, **kwargs)
        assert pending
        barrier.wait(timeout=5)
        return pending

    def submit(*args, **kwargs):
        calls.append(1)
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(idle, "query_pending", hint)
    monkeypatch.setattr(query, "submit", submit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(dispatch.advance, config, value["id"], 1) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert calls == [1]
    with channels.readonly(config) as db:
        assert db.execute("SELECT state FROM events WHERE event_id='one'").fetchone()[0] == "ready"


def test_ambiguous_send_before_watermark_still_requires_reconciliation(config, channel):
    value, _ = channel
    bind(config, value)
    assert dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询：压力")
    with channels.opened(config) as db:
        db.execute("UPDATE events SET state='unknown',revision=0,created=0")
    policy.change(config, value["id"], "query", 0, enabled=True)
    with pytest.raises(InvalidInput, match="reconciliation"):
        dispatch.prepare_delivery(config, value["id"], 1)


def test_model_claim_rechecks_disabled_service(config, channel, monkeypatch):
    value, _ = channel
    current = bind(config, value)
    with channels.opened(config) as db:
        settings = {**current["value"], "answer_mode": "model"}
        db.execute("UPDATE channels SET value=? WHERE id=?", (json.dumps(settings), value["id"]))
    policy.change(config, value["id"], "query", 0, enabled=True)
    assert dispatch.capture(config, value["id"], 1, "one", "ou_synthetic", "查询：压力")
    _before_claim(
        monkeypatch,
        lambda: policy.change(config, value["id"], "query", 1, enabled=False),
    )
    monkeypatch.setattr(
        model_answer.model_call, "complete", lambda *args: pytest.fail("disabled model called")
    )
    dispatch.advance(config, value["id"], 1, independent=True)
    with channels.readonly(config) as db:
        assert db.execute("SELECT state FROM events WHERE event_id='one'").fetchone()[0] == (
            "captured"
        )


@pytest.mark.parametrize("receipt", [False, True])
@pytest.mark.parametrize("change", ["policy", "revoke", "claimed", "watermark", "service"])
def test_intake_claim_rechecks_after_positive_hint(config, channel, monkeypatch, receipt, change):
    configured, _ = channel
    current = bind(config, configured)
    with database.connect(config.state_root) as main:
        main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (current["value"]["vault_id"],))
        main.execute(
            "UPDATE client_vault_grants SET can_add=1 WHERE client_id=?",
            (current["value"]["client_id"],),
        )
    with channels.opened(config) as db:
        db.execute(
            "UPDATE channels SET value=? WHERE id=?",
            (json.dumps({**current["value"], "add": True}), current["id"]),
        )
        current = channels.get(db, current["id"])
    entry = current, intake_repository.binding(current)
    enabled = enable(config, entry, fetch_articles=True, receipt=True, outbound_limit=10)
    identity = entry[0]["id"]
    if change in {"watermark", "service"}:
        policy.change(config, identity, "intake", 0, enabled=True)
    value = {
        "extraction": "pending",
        "links": [{"original_url": "https://example.invalid/a", "allowed": False}],
    }
    with channels.opened(config) as db:
        intake_processing.record(
            db, identity, "synthetic", enabled["revision"], "stored", None, value
        )

    def changed():
        if change == "revoke":
            with database.connect(config.state_root) as main:
                main.execute(
                    "UPDATE client_vault_grants SET can_add=0 WHERE client_id=?",
                    (entry[1].client_id,),
                )
        elif change == "service":
            policy.change(config, identity, "intake", 1, enabled=False)
        else:
            with channels.opened(config) as db:
                if change == "policy":
                    db.execute("UPDATE channel_intake_policies SET revision=revision+1")
                elif change == "claimed":
                    revised = (
                        {**value, "receipt_state": "unknown"}
                        if receipt
                        else {**value, "links": [{**value["links"][0], "state": "extracting"}]}
                    )
                    db.execute("UPDATE channel_intake_records SET value=?", (json.dumps(revised),))
                else:
                    service = policy.get(config, identity, "intake")
                    service["admit_after"] = time.time() + 60
                    db.execute("UPDATE channel_services SET value=?", (json.dumps(service),))

    _before_claim(monkeypatch, changed)
    claim = intake_receipts.take if receipt else intake_extraction.advance
    if change in {"policy", "revoke"}:
        with pytest.raises((InvalidInput, auth.Denied)):
            claim(config, identity)
    elif receipt and change == "service":
        # Processing pause does not suppress acknowledgements for already stored work.
        assert claim(config, identity) is not None
    else:
        assert claim(config, identity) is (None if receipt else False)
    with channels.readonly(config) as db:
        assert db.execute("SELECT count(*) FROM channel_intake_consumption").fetchone()[0] == (
            1 if receipt and change == "service" else 0
        )
