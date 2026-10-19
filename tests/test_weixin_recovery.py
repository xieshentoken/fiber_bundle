"""Append-only recovery against actual temporary SQLite journals and frozen batches."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows

from fiber_bundle import auth, database
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_recovery as recovery
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.weixin_events import MAX_BYTES


@pytest.fixture
def receipt(config, binding):
    recovery.initialize(config, binding)
    response = poll(message(item_list=[{"type": 2}, {"type": 5}]))
    result = inbox.receive(config, binding, "", response)
    job = result["events"][0]["job_id"]
    return job, recovery.view(config, binding, job)["raw_sha256"], response, result


def test_partial_then_complete_preserves_receipt_cursor_and_replay(config, binding, receipt):
    job, sha, response, first = receipt
    original = rows(config, "events")
    assert recovery.complete(config, binding, job, sha, {"0": b"image"})["state"] == "incomplete"
    result = recovery.complete(config, binding, job, sha, {"1": b"document"})
    assert result["state"] == "pending" and result["missing_items"] == []
    recovery.initialize(config, binding)
    assert recovery.complete(config, binding, job, sha, {"1": b"document"}) == result
    assert recovery.view(config, binding, job) == result
    assert rows(config, "events") == original and rows(config, "media") == []
    assert inbox.cursor(config, binding) == "next"
    assert inbox.receive(config, binding, "", response) == first


def test_frozen_batch_becomes_ready_without_changing_members(config, binding, receipt):
    job, sha, _, _ = receipt
    batches.initialize(config, binding)
    batch = batches.advance(config, binding, now=datetime.now(UTC) + timedelta(seconds=5))[0]
    assert not batch["content_ready"]
    recovery.complete(config, binding, job, sha, {"0": b"image", "1": b"document"})
    current = batches.get(config, binding, batch["batch_id"])
    assert current["content_ready"] and current["state"] == "frozen"
    assert current["members"][0]["job_id"] == job
    assert batches.choose(config, binding, batch["batch_id"], "save_one")["dispatched"] is False
    assert rows(config, "events")[0]["state"] == "incomplete"


@pytest.mark.parametrize(
    "attachments", [{}, {"9": b"x"}, {"0": b""}, {"0": "x"}, {"0": b"x" * (MAX_BYTES + 1)}]
)
def test_invalid_recovery_is_atomic(config, binding, receipt, attachments):
    job, sha, _, _ = receipt
    with pytest.raises(InvalidInput):
        recovery.complete(config, binding, job, sha, attachments)
    assert rows(config, "wx_recovered_media") == []


def test_wrong_binding_and_replacement_are_refused(config, binding, receipt):
    job, sha, _, _ = receipt
    with pytest.raises(InvalidInput, match="binding"):
        recovery.complete(config, binding, job, "wrong", {"0": b"image"})
    recovery.complete(config, binding, job, sha, {"0": b"image"})
    with pytest.raises(IntegrityError, match="replacement"):
        recovery.complete(config, binding, job, sha, {"0": b"other", "1": b"document"})
    assert recovery.view(config, binding, job)["missing_items"] == ["1"]


def test_original_bytes_cannot_be_replaced(config, binding):
    recovery.initialize(config, binding)
    response = poll(message(item_list=[{"type": 2}, {"type": 5}]))
    job = inbox.receive(config, binding, "", response, {"one": {"0": b"original"}})["events"][0][
        "job_id"
    ]
    sha = recovery.view(config, binding, job)["raw_sha256"]
    with pytest.raises(IntegrityError):
        recovery.complete(config, binding, job, sha, {"0": b"replacement"})
    recovery.complete(config, binding, job, sha, {"0": b"original", "1": b"document"})
    assert len(rows(config, "media")) == len(rows(config, "wx_recovered_media")) == 1


def test_unknown_type_remains_unsupported_after_media_arrives(config, binding):
    recovery.initialize(config, binding)
    job = inbox.receive(config, binding, "", poll(message(item_list=[{"type": 99}, {"type": 2}])))[
        "events"
    ][0]["job_id"]
    sha = recovery.view(config, binding, job)["raw_sha256"]
    assert recovery.complete(config, binding, job, sha, {"1": b"image"})["state"] == "unsupported"


def test_cancelled_batch_blocks_recovery(config, binding, receipt):
    job, sha, _, _ = receipt
    batches.initialize(config, binding)
    batch = batches.advance(config, binding, now=datetime.now(UTC) + timedelta(seconds=5))[0]
    batches.choose(config, binding, batch["batch_id"], "cancel")
    with pytest.raises(InvalidInput, match="cancelled"):
        recovery.complete(config, binding, job, sha, {"0": b"image"})


def test_revoked_access_blocks_view_and_complete(config, binding, receipt):
    job, sha, _, _ = receipt
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=False, original=False, add=False)
    db.close()
    with pytest.raises(auth.Denied):
        recovery.view(config, binding, job)
    with pytest.raises(auth.Denied):
        recovery.complete(config, binding, job, sha, {"0": b"image"})


def test_write_failure_rolls_back_all_supplements(config, binding, receipt):
    job, sha, _, _ = receipt
    with inbox.connection(config, binding) as db:
        db.execute(
            "CREATE TRIGGER fail_recovery BEFORE INSERT ON wx_recovered_media "
            "WHEN NEW.item_key='1' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        recovery.complete(config, binding, job, sha, {"0": b"image", "1": b"document"})
    assert rows(config, "wx_recovered_media") == []
    with inbox.connection(config, binding) as db:
        db.execute("DROP TRIGGER fail_recovery")
    assert (
        recovery.complete(config, binding, job, sha, {"0": b"image", "1": b"doc"})["state"]
        == "pending"
    )


def test_concurrent_duplicate_recovery_is_idempotent(config, binding, receipt):
    job, sha, _, _ = receipt
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: recovery.complete(config, binding, job, sha, {"0": b"image"}), range(2)
            )
        )
    assert results[0] == results[1] and len(rows(config, "wx_recovered_media")) == 1


def test_corrupt_recovered_bytes_cannot_enable_batch(config, binding, receipt):
    job, sha, _, _ = receipt
    recovery.complete(config, binding, job, sha, {"0": b"image"})
    with inbox.connection(config, binding) as db:
        db.execute("UPDATE wx_recovered_media SET data=?", (b"tampered",))
    with pytest.raises(IntegrityError):
        recovery.view(config, binding, job)
