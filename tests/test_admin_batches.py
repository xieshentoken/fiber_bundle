"""Admin batch choices use the durable pre-dispatch journal only."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll

from fiber_bundle import weixin_batches, weixin_inbox

BASE = datetime(2026, 9, 28, tzinfo=UTC)


def capture(config, binding, event_id, seconds):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return BASE + timedelta(seconds=seconds)

    with patch.object(weixin_inbox, "datetime", Clock):
        return weixin_inbox.receive(
            config,
            binding,
            weixin_inbox.cursor(config, binding),
            poll(message(event_id), cursor=event_id),
        )


def test_admin_batch_list_requires_login_and_empty_journal_is_safe(http):
    assert http.get("/admin/batches/pending").json() == []
    http.cookies.clear()
    assert http.get("/admin/batches/pending").status_code == 401


@pytest.mark.parametrize("action", ["save_one", "save_separately", "consult", "cancel"])
def test_admin_choice_records_scope_without_publication(http, config, binding, action):
    weixin_batches.initialize(config, binding)
    capture(config, binding, "first", 0)
    capture(config, binding, "second", 1)
    batch_id = weixin_batches.advance(config, binding, now=BASE + timedelta(seconds=4))[0][
        "batch_id"
    ]
    route = f"/admin/batches/{batch_id}/choice"
    listed = http.get("/admin/batches/pending").json()
    assert listed[0]["id"] == batch_id
    assert listed[0]["count"] == 2
    assert listed[0]["state"] == "frozen"
    assert "合成正文" in listed[0]["preview"]
    assert (
        http.post(route, json={"action": action}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    chosen = http.post(route, json={"action": action})
    assert chosen.status_code == 200
    assert chosen.json()["dispatched"] is False
    assert chosen.json()["action"] == action
    assert http.post(route, json={"action": action}).json() == chosen.json()
    remaining = http.get("/admin/batches/pending").json()
    assert len(remaining) == (1 if action == "consult" else 0)
    if action == "consult":
        assert remaining[0]["needs_question"] is True
        answered = http.post(route, json={"action": "consult", "question": "合成问题"})
        assert answered.status_code == 200
        assert answered.json()["needs_question"] is False
        assert http.get("/admin/batches/pending").json() == []


def test_buffering_and_missing_content_block_save(http, config, binding):
    weixin_batches.initialize(config, binding)
    capture(config, binding, "first", 0)
    batch = weixin_batches.advance(config, binding, now=BASE)[0]
    route = f"/admin/batches/{batch['batch_id']}/choice"
    assert http.get("/admin/batches/pending").json()[0]["state"] == "buffering"
    assert http.post(route, json={"action": "save_one"}).status_code == 422
    assert http.post(route, json={"action": "unknown"}).status_code == 422


def test_revoked_ingress_cannot_select_save_but_admin_can_cancel(http, config, binding):
    weixin_batches.initialize(config, binding)
    capture(config, binding, "first", 0)
    batch_id = weixin_batches.advance(config, binding, now=BASE + timedelta(seconds=4))[0][
        "batch_id"
    ]
    assert http.post(f"/admin/clients/{binding.client_id}/revoke").status_code == 200
    route = f"/admin/batches/{batch_id}/choice"
    assert http.post(route, json={"action": "save_one"}).status_code == 404
    cancelled = http.post(route, json={"action": "cancel"})
    assert cancelled.status_code == 200
    assert cancelled.json()["dispatched"] is False


def test_admin_deadline_tracks_quiet_window_without_freezing_on_read(http, config, binding):
    weixin_batches.initialize(config, binding)
    capture(config, binding, "first", 0)
    weixin_batches.advance(config, binding, now=BASE)
    first = http.get("/admin/batches/pending").json()[0]
    assert first["state"] == "buffering"
    assert first["freeze_at"] == (BASE + timedelta(seconds=3)).timestamp()
    # Today's HTTP read is beyond BASE; it must not change the durable state.
    assert http.get("/admin/batches/pending").json()[0] == first
    capture(config, binding, "second", 2)
    weixin_batches.advance(config, binding, now=BASE + timedelta(seconds=2))
    updated = http.get("/admin/batches/pending").json()[0]
    assert updated["id"] == first["id"]
    assert updated["freeze_at"] == (BASE + timedelta(seconds=5)).timestamp()
    weixin_batches.advance(config, binding, now=BASE + timedelta(seconds=5))
    frozen = http.get("/admin/batches/pending").json()[0]
    assert frozen["state"] == "frozen"
    assert frozen["freeze_at"] is None
