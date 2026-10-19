"""Exercise the production query-only poll loop with iLink's zero-field omission."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle.channels import store, weixin


def adapter(config, channel, monkeypatch, response):
    value, _ = channel
    current = bind(config, value)
    current["value"].update(platform="weixin", natural_questions=True)
    with store.opened(config) as db:
        db.execute(
            "UPDATE channels SET value=? WHERE id=?", (json.dumps(current["value"]), value["id"])
        )
    home = store.home(config, value["id"])
    (home / "weixin-login.json").write_text(
        json.dumps(
            {
                "owner_id": current["owner"],
                "account_id": "synthetic@im.bot",
                "base_url": "https://ilinkai.weixin.qq.com",
            }
        )
    )
    holder, polls = {}, []

    class Base:
        def __init__(self, config):
            self._running = True
            self._poll_session = None
            self._base_url = "https://ilinkai.weixin.qq.com"
            self._token = "synthetic"
            self._account_id = "synthetic@im.bot"

    async def updates(*args, **kwargs):
        polls.append(kwargs["sync_buf"])
        # Replay the same platform messages once; the event ledger must deduplicate them.
        if len(polls) == 2:
            holder["adapter"]._running = False
        return copy.deepcopy(response)

    async def connect(*args, **kwargs):
        return True

    module = SimpleNamespace(WeixinAdapter=Base, _get_updates=updates)
    monkeypatch.setattr(weixin, "upstream", lambda: module)
    monkeypatch.setattr(weixin, "query_connect", lambda _: connect)
    original = weixin.importlib.import_module
    monkeypatch.setattr(
        weixin.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(PlatformConfig=lambda **kwargs: kwargs)
            if name == "gateway.config"
            else original(name)
        ),
    )
    result = weixin.adapter(config, current)
    holder["adapter"] = result
    return result, home, polls


def messages():
    return [
        {
            "message_id": number,
            "from_user_id": "ou_synthetic",
            "to_user_id": "synthetic@im.bot",
            "context_token": "synthetic-context",
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        for number, text in enumerate(
            [
                "请概括薄膜沸腾的主要结论。",
                "请根据 Book 中的传热专家包列出常见材料热导率。",
            ],
            start=101,
        )
    ]


@pytest.mark.parametrize("status", [{}, {"ret": 0}, {"errcode": 0}, {"ret": 0, "errcode": 0}])
def test_successful_poll_may_omit_zero_status_fields_and_replay_is_deduplicated(
    config, channel, monkeypatch, status
):
    response = {**status, "msgs": messages(), "get_updates_buf": "synthetic-next"}
    runtime, home, polls = adapter(config, channel, monkeypatch, response)
    asyncio.run(runtime._poll_loop())
    assert runtime._fiber_error is None
    assert polls == ["", "synthetic-next"]
    assert json.loads((home / "channel-cursor.json").read_text())["cursor"] == "synthetic-next"
    with store.readonly(config) as db:
        events = db.execute("SELECT event_id,state FROM events ORDER BY event_id").fetchall()
    assert [(r["event_id"], r["state"]) for r in events] == [
        ("101", "captured"),
        ("102", "captured"),
    ]


@pytest.mark.parametrize(
    "status",
    [
        {"ret": 1},
        {"ret": False},
        {"ret": "0"},
        {"ret": None},
        {"errcode": 7},
        {"errcode": False},
        {"errcode": "0"},
        {"errcode": None},
    ],
)
def test_present_invalid_or_rejected_status_never_captures_or_advances(
    config, channel, monkeypatch, status
):
    response = {**status, "msgs": messages(), "get_updates_buf": "synthetic-next"}
    runtime, home, polls = adapter(config, channel, monkeypatch, response)
    asyncio.run(runtime._poll_loop())
    assert runtime._fiber_error
    assert len(polls) == 1
    assert not (home / "channel-cursor.json").exists()
    with store.readonly(config) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_empty_cursor_keeps_prior_position(config, channel, monkeypatch):
    runtime, home, polls = adapter(config, channel, monkeypatch, {"get_updates_buf": ""})
    (home / "channel-cursor.json").write_text(json.dumps({"cursor": "previous"}))
    asyncio.run(runtime._poll_loop())
    assert polls == ["previous", "previous"]
    assert json.loads((home / "channel-cursor.json").read_text())["cursor"] == "previous"


def test_capture_failure_stops_without_retry_or_cursor_advance(config, channel, monkeypatch):
    runtime, home, polls = adapter(config, channel, monkeypatch, {"msgs": messages()})

    def failed(*args):
        raise OSError("synthetic private disk diagnostic")

    monkeypatch.setattr(weixin.dispatch, "capture", failed)
    asyncio.run(runtime._poll_loop())
    assert len(polls) == 1
    assert not (home / "channel-cursor.json").exists()
    assert isinstance(runtime._fiber_exception, OSError)
