import asyncio
from datetime import timedelta

import pytest
from test_admin_batches import BASE, capture
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    auth,
    batch_reminders,
    database,
    weixin_batches,
    weixin_confirmations,
    weixin_inbox,
)
from fiber_bundle.config import InvalidInput


@pytest.fixture
def pending(config, binding):
    weixin_confirmations.initialize(config, binding)
    ids = []
    for number in range(2):
        capture(config, binding, str(number), number * 10)
        weixin_confirmations.process(config, binding)
        batch = weixin_batches.advance(
            config, binding, now=BASE + timedelta(seconds=number * 10 + 4)
        )[0]["batch_id"]
        prompt = weixin_confirmations.prepare(config, binding, batch)
        with weixin_inbox.connection(config, binding) as db:
            db.execute(
                "UPDATE wx_confirmation_prompts SET state='sent' WHERE id=?", (prompt["prompt_id"],)
            )
            db.commit()
        ids.append(batch)
    return ids


def sender(sent):
    async def send(owner, body, authorize):
        authorize()
        sent.append((owner, body))
        return True

    return send


def test_digest_24_hour_single_summary_restart_new_day(config, binding, pending):
    sent = []
    send = sender(sent)
    start = BASE.timestamp()
    assert (
        asyncio.run(batch_reminders.deliver(config, binding, send, now=start + 86399))["state"]
        == "idle"
    )
    assert (
        asyncio.run(batch_reminders.deliver(config, binding, send, now=start + 86420))["state"]
        == "sent"
    )
    assert len(sent) == 1 and "2 项" in sent[0][1]
    for clock in [start + 86421, start + 90000, start + 100000, start - 100]:
        assert (
            asyncio.run(batch_reminders.deliver(config, binding, send, now=clock))["state"]
            == "idle"
        )
    assert (
        asyncio.run(batch_reminders.deliver(config, binding, send, now=start + 172820))["state"]
        == "sent"
    )
    assert len(sent) == 2
    db = database.connect(config.state_root)
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_per_batch_mute_preserves_pending_and_history(config, binding, pending, http):
    batch = pending[0]
    assert (
        http.put(
            "/admin/backend/reminders/" + batch,
            json={"enabled": False},
            headers={"X-CSRF-Token": "bad"},
        ).status_code
        == 403
    )
    assert (
        http.put("/admin/backend/reminders/" + batch, json={"enabled": False}).json()["enabled"]
        is False
    )
    assert len(http.get("/admin/backend/reminders").json()) == 2
    assert weixin_batches.get(config, binding, batch)["state"] == "frozen"
    sent = []
    assert (
        asyncio.run(
            batch_reminders.deliver(config, binding, sender(sent), now=BASE.timestamp() + 86500)
        )["state"]
        == "sent"
    )
    assert "1 项" in sent[0][1]


def test_choice_and_cancel_do_not_get_periodic_notifications(config, binding, pending):
    weixin_batches.choose(config, binding, pending[0], "cancel")
    weixin_batches.choose(config, binding, pending[1], "save_one")
    assert (
        asyncio.run(
            batch_reminders.deliver(config, binding, sender([]), now=BASE.timestamp() + 86500)
        )["state"]
        == "idle"
    )


def test_unknown_send_blocks_replay_and_next_day(config, binding, pending):
    count = 0

    async def unknown(*args):
        nonlocal count
        count += 1
        args[2]()
        return False

    assert (
        asyncio.run(
            batch_reminders.deliver(config, binding, unknown, now=BASE.timestamp() + 86500)
        )["state"]
        == "unknown"
    )
    saved = batch_reminders.history(config)
    assert saved[0]["state"] == "unknown" and saved[0]["batch_count"] == 2
    assert len(saved[0]["body_sha256"]) == 64
    with pytest.raises(InvalidInput, match="unresolved"):
        asyncio.run(
            batch_reminders.deliver(config, binding, unknown, now=BASE.timestamp() + 200000)
        )
    assert count == 1


def test_cancel_while_waiting_for_send_gate_rechecked(config, binding, pending):
    posted = []

    async def racing(owner, body, authorize):
        weixin_batches.choose(config, binding, pending[0], "cancel")
        authorize()
        posted.append(body)
        return True

    assert (
        asyncio.run(batch_reminders.deliver(config, binding, racing, now=BASE.timestamp() + 86500))[
            "state"
        ]
        == "unknown"
    )
    assert not posted


def test_permission_revocation_before_post_no_delivery(config, binding, pending):
    posted = []

    async def revoked(owner, body, authorize):
        db = database.connect(config.state_root)
        auth.revoke_client(db, binding.client_id)
        db.close()
        authorize()
        posted.append(body)
        return True

    with pytest.raises(auth.Denied):
        asyncio.run(batch_reminders.deliver(config, binding, revoked, now=BASE.timestamp() + 86500))
    assert not posted
