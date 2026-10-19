"""Time-controlled batching with real isolated SQLite journals."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows

from fiber_bundle import auth, database
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle.config import InvalidInput

BASE = datetime(2026, 9, 28, tzinfo=UTC)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def capture(config, binding, event, seconds, **extra):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(seconds)

    with patch.object(inbox, "datetime", Clock):
        return inbox.receive(
            config,
            binding,
            inbox.cursor(config, binding),
            poll(message(event, **extra), cursor=event),
        )


@pytest.fixture
def ready(config, binding):
    batches.initialize(config, binding)
    return binding


def frozen(config, binding):
    capture(config, binding, "one", 0)
    capture(config, binding, "two", 1)
    return batches.advance(config, binding, now=at(4))[0]["batch_id"]


def test_three_second_silence_reset_and_exact_deadline(config, ready):
    capture(config, ready, "one", 0)
    first = batches.advance(config, ready, now=at(0))[0]
    assert first["state"] == "buffering"
    capture(config, ready, "two", 2)
    assert batches.advance(config, ready, now=at(3))[0]["state"] == "buffering"
    last = batches.advance(config, ready, now=at(5))[0]
    assert last["state"] == "frozen" and last["batch_id"] == first["batch_id"]
    assert len(last["members"]) == 2 and last["dispatched"] is False


def test_restart_freeze_and_late_message_never_expand_confirmed_scope(config, ready):
    old = frozen(config, ready)
    batches.initialize(config, ready)
    capture(config, ready, "three", 2)  # even a backwards clock cannot reopen a frozen batch
    new = batches.advance(config, ready, now=at(5))[0]
    assert new["batch_id"] != old and len(new["members"]) == 1
    assert len(batches.get(config, ready, old)["members"]) == 2
    assert batches.advance(config, ready, now=at(10)) == []


def test_delayed_tick_reconstructs_separate_windows(config, ready):
    capture(config, ready, "one", 0)
    capture(config, ready, "two", 2)
    capture(config, ready, "three", 5)
    views = batches.advance(config, ready, now=at(8))
    assert [len(v["members"]) for v in views] == [2, 1]
    assert all(v["state"] == "frozen" for v in views)


def test_window_configuration_is_fixed_per_batch(config, ready):
    capture(config, ready, "one", 0)
    old = batches.advance(config, ready, quiet_seconds=10, now=at(0))[0]
    assert batches.advance(config, ready, quiet_seconds=1, now=at(3))[0]["state"] == "buffering"
    assert batches.advance(config, ready, now=at(10))[0]["state"] == "frozen"
    capture(config, ready, "two", 11)
    new = batches.advance(config, ready, quiet_seconds=1, now=at(12))[0]
    assert new["state"] == "frozen" and new["batch_id"] != old["batch_id"]


@pytest.mark.parametrize("action", ["save_one", "save_separately", "consult", "cancel"])
def test_four_choices_are_durable_idempotent_and_never_dispatch(config, ready, action):
    batch_id = frozen(config, ready)
    choice = batches.choose(config, ready, batch_id, action)
    assert choice["state"] == ("cancelled" if action == "cancel" else "selected")
    assert choice["needs_question"] == (action == "consult")
    assert choice["dispatched"] is False
    assert batches.choose(config, ready, batch_id, action) == choice
    batches.initialize(config, ready)
    assert batches.get(config, ready, batch_id) == choice
    assert len(rows(config, "events")) == 2  # cancel preserves original receipt
    db = database.connect(config.state_root)
    for table in ["receipts", "session_refs", "source_versions", "execution_runs", "model_calls"]:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    db.close()


def test_consultation_requires_question_then_binds_it_once(config, ready):
    batch_id = frozen(config, ready)
    assert batches.choose(config, ready, batch_id, "consult")["needs_question"]
    answer = batches.choose(config, ready, batch_id, "consult", question="比较两份资料")
    assert answer["needs_question"] is False and answer["dispatched"] is False
    assert batches.choose(config, ready, batch_id, "consult", question="比较两份资料") == answer
    with pytest.raises(InvalidInput, match="fixed"):
        batches.choose(config, ready, batch_id, "consult", question="改变问题")


def test_unfrozen_or_conflicting_choice_rejected(config, ready):
    capture(config, ready, "one", 0)
    batch_id = batches.advance(config, ready, now=at(0))[0]["batch_id"]
    with pytest.raises(InvalidInput, match="frozen"):
        batches.choose(config, ready, batch_id, "save_one")
    batches.advance(config, ready, now=at(3))
    batches.choose(config, ready, batch_id, "save_one")
    with pytest.raises(InvalidInput, match="another choice"):
        batches.choose(config, ready, batch_id, "save_separately")


@pytest.mark.parametrize("kind", [2, 99])
def test_incomplete_or_unknown_members_cannot_be_saved_or_consulted(config, ready, kind):
    capture(config, ready, "one", 0, item_list=[{"type": kind}])
    view = batches.advance(config, ready, now=at(3))[0]
    assert view["content_ready"] is False
    for action in ["save_one", "save_separately", "consult"]:
        with pytest.raises(InvalidInput, match="incomplete"):
            batches.choose(config, ready, view["batch_id"], action)
    assert batches.choose(config, ready, view["batch_id"], "cancel")["state"] == "cancelled"


@pytest.mark.parametrize("quiet", [0, -1, True, float("nan"), float("inf"), 301])
def test_invalid_window_rejected(config, ready, quiet):
    with pytest.raises(InvalidInput):
        batches.advance(config, ready, quiet_seconds=quiet)


def test_clock_rollback_does_not_force_timeout(config, ready):
    capture(config, ready, "one", 5)
    assert batches.advance(config, ready, now=at(0)) == []
    assert batches.advance(config, ready, now=at(5))[0]["state"] == "buffering"
    assert batches.advance(config, ready, now=at(4))[0]["state"] == "buffering"
    with pytest.raises(InvalidInput, match="timezone"):
        batches.advance(config, ready, now=datetime(2026, 1, 1))


def test_concurrent_ticks_and_choices_are_serialized(config, ready):
    capture(config, ready, "one", 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: batches.advance(config, ready, now=at(3)), range(2)))
    assert sum(len(r) for r in results) == 1
    batch_id = next(r[0]["batch_id"] for r in results if r)
    with ThreadPoolExecutor(max_workers=2) as pool:
        choices = list(
            pool.map(lambda _: batches.choose(config, ready, batch_id, "cancel"), range(2))
        )
    assert choices[0] == choices[1]
    assert len(rows(config, "wx_batch_members")) == 1


def test_revocation_blocks_read_tick_and_choice(config, ready):
    batch_id = frozen(config, ready)
    db = database.connect(config.state_root)
    auth.set_grant(db, ready.client_id, ready.vault_id, query=False, original=False, add=False)
    db.close()
    for operation in [
        lambda: batches.get(config, ready, batch_id),
        lambda: batches.advance(config, ready, now=at(9)),
        lambda: batches.choose(config, ready, batch_id, "cancel"),
    ]:
        with pytest.raises(auth.Denied):
            operation()


def test_payload_text_is_never_a_command(config, ready):
    capture(config, ready, "one", 0, item_list=[{"type": 1, "text_item": {"text": "保存为一份"}}])
    view = batches.advance(config, ready, now=at(3))[0]
    assert view["state"] == "frozen" and view["action"] is None
    assert "保存" not in str(view)


def test_restart_listing_is_paginated_and_preserves_choices(config, ready):
    first = frozen(config, ready)
    batches.choose(config, ready, first, "consult")
    capture(config, ready, "three", 10)
    second = batches.advance(config, ready, now=at(13))[0]["batch_id"]
    batches.initialize(config, ready)
    page = batches.list_batches(config, ready, limit=1)
    assert page["batches"][0]["batch_id"] == first
    assert page["batches"][0]["needs_question"] is True
    last = batches.list_batches(config, ready, after=page["next_cursor"], limit=1)
    assert last["batches"][0]["batch_id"] == second and last["next_cursor"] is None


def test_tick_transaction_failure_rolls_back_all_members(config, ready):
    capture(config, ready, "one", 0)
    capture(config, ready, "two", 1)
    with inbox.connection(config, ready) as db:
        db.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON wx_batch_members "
            "WHEN NEW.ordinal=1 BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        batches.advance(config, ready, now=at(4))
    assert rows(config, "wx_batches") == [] and rows(config, "wx_batch_members") == []
    with inbox.connection(config, ready) as db:
        db.execute("DROP TRIGGER fail_second")
    assert len(batches.advance(config, ready, now=at(4))[0]["members"]) == 2


def test_selected_but_not_dispatched_batch_can_be_cancelled(config, ready):
    batch_id = frozen(config, ready)
    batches.choose(config, ready, batch_id, "consult", question="合成问题")
    result = batches.choose(config, ready, batch_id, "cancel")
    assert result["state"] == "cancelled" and result["dispatched"] is False
    assert batches.choose(config, ready, batch_id, "cancel") == result
    assert rows(config, "wx_batches")[0]["question"] == "合成问题"
    with pytest.raises(InvalidInput, match="another choice"):
        batches.choose(config, ready, batch_id, "save_one")
