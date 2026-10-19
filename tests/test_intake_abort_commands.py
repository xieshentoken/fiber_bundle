"""Owner-only abort is a durable command, never a new knowledge source."""

import json
from dataclasses import replace

import pytest
from test_channel_intake import enable, receive
from test_feishu_ingress import payload

from fiber_bundle import feishu_receiver, intake_abort_store, weixin_inbox
from fiber_bundle.channels import intake_processing
from fiber_bundle.config import InvalidInput

pytest_plugins = ["test_channel_intake"]


@pytest.mark.parametrize(
    "text,target",
    [
        ("abort", None),
        (" /ABORT ", None),
        ("abort 1234abcd", "1234abcd"),
        ("abort all", "invalid"),
        ("abort 1234abcd extra", "invalid"),
    ],
)
def test_command_syntax(text, target):
    assert intake_abort_store.parse(text) == (True, target)
    assert intake_abort_store.parse("the article says abort") == (False, None)


def command(config, entry, text, identity="abort-event"):
    return feishu_receiver.capture(
        config, entry[1], payload(body={"text": text}, identity=identity)
    )


def test_abort_without_query_routing_resolves_batch_and_deduplicates(config, entry):
    enable(config, entry, policy_version=2, fetch_articles=True)
    batch = receive(config, entry)
    intake_processing.prepare(config, entry[0]["id"])
    expected = {"state": "intake_abort_recorded"}
    assert command(config, entry, "abort " + batch[:8]) == expected
    assert command(config, entry, "abort " + batch[:8]) == expected
    with weixin_inbox.connection(config, entry[1]) as db:
        row = db.execute("SELECT * FROM fiber_intake_abort_commands").fetchone()
        assert row["state"] == "requested"
        assert json.loads(row["scope"])["batch"] == batch
        assert db.execute("SELECT count(*) FROM fiber_intake_abort_commands").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM events WHERE event_id='abort-event'").fetchone()
    with pytest.raises(InvalidInput, match="event_changed"):
        command(config, entry, "abort 1234abcd")


def test_non_owner_and_shared_cards_cannot_issue_abort(config, entry):
    enable(config, entry, policy_version=2)
    event = payload(body={"text": "abort"})
    event["event"]["sender"]["sender_id"]["open_id"] = "ou_other"
    assert feishu_receiver.capture(config, entry[1], event)["state"] == "ignored"
    card = payload(kind="post", body={"content": [[{"tag": "text", "text": "abort"}]]})
    assert feishu_receiver.capture(config, entry[1], card).get("state") != "intake_abort_recorded"
    with weixin_inbox.connection(config, entry[1]) as db:
        assert db.execute("SELECT count(*) FROM fiber_intake_abort_commands").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_wrong_channel_task_number_is_not_disclosed(config, entry):
    enable(config, entry, policy_version=2)
    batch = receive(config, entry)
    other = replace(entry[1], account_id="feishu:cli_other")
    feishu_receiver.initialize(config, other)
    event = payload(body={"text": "abort " + batch}, identity="other-abort")
    event["header"]["app_id"] = "cli_other"
    assert feishu_receiver.capture(config, other, event)["state"] == "intake_abort_recorded"
    with weixin_inbox.connection(config, other) as db:
        row = db.execute(
            "SELECT * FROM fiber_intake_abort_commands WHERE account_id=?", (other.account_id,)
        ).fetchone()
        assert row["state"] == "not_found" and json.loads(row["scope"]) == {}
