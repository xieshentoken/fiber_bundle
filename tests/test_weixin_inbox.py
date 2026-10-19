"""Isolated ingress transactions, exact owner binding and pre-confirmation quarantine."""

import copy
import errno
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from fiber_bundle import auth, database
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.weixin_events import MAX_BYTES, Binding, prepare


@pytest.fixture
def binding(config, store):
    db = database.connect(config.state_root)
    client = auth.create_client(db, "synthetic ingress", False)
    vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    auth.set_grant(db, client["id"], vid, query=False, original=False, add=True)
    db.close()
    value = Binding("synthetic-bot", "synthetic-owner", client["id"], vid)
    inbox.initialize(config, value)
    return value


def message(event="one", **extra):
    return {
        "message_id": event,
        "from_user_id": "synthetic-owner",
        "to_user_id": "synthetic-bot",
        "context_token": "synthetic-private-route",
        "item_list": [{"type": 1, "text_item": {"text": "合成正文"}}],
        **extra,
    }


def poll(*messages, cursor="next"):
    return {"ret": 0, "get_updates_buf": cursor, "msgs": list(messages)}


def rows(config, table):
    db = sqlite3.connect(config.state_root / "weixin-inbox.sqlite3")
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
    finally:
        db.close()


def test_multitext_and_media_preserved_private_and_not_dispatched(config, binding, capsys):
    event = message(
        item_list=[
            {"type": 1, "text_item": {"text": "第一段"}},
            {"type": 1, "text_item": {"text": "第二段"}},
            {"type": 2, "image_item": {"media": "synthetic"}},
        ]
    )
    response = poll(event)
    result = inbox.receive(config, binding, "", response, {"one": {"2": b"synthetic image bytes"}})
    assert result["dispatched"] is False and result["events"][0]["state"] == "pending"
    assert inbox.cursor(config, binding) == "next"
    assert json.loads(rows(config, "events")[0]["raw"]) == event
    assert rows(config, "media")[0]["data"] == b"synthetic image bytes"
    assert (
        inbox.receive(config, binding, "", response, {"one": {"2": b"synthetic image bytes"}})
        == result
    )
    assert "synthetic-private-route" not in json.dumps(result) and "合成正文" not in json.dumps(
        result
    )
    assert capsys.readouterr().out == ""
    assert os.stat(config.state_root / "weixin-inbox.sqlite3").st_mode & 0o777 == 0o600
    db = database.connect(config.state_root)
    for table in [
        "receipts",
        "session_refs",
        "source_versions",
        "execution_runs",
        "model_calls",
        "article_publications",
    ]:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert db.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
    db.close()


def test_replay_restart_and_identical_text_new_id_preserved(config, binding):
    response = poll(message())
    first = inbox.receive(config, binding, "", response)
    inbox.initialize(config, binding)
    assert inbox.receive(config, binding, "", response) == first
    later = inbox.receive(config, binding, "next", poll(message("two"), cursor="next2"))
    assert later["events"][0]["job_id"] != first["events"][0]["job_id"]
    assert len(rows(config, "events")) == 2
    with pytest.raises(InvalidInput, match="stale"):
        inbox.receive(config, binding, "", poll(message("old"), cursor="old"))
    assert inbox.cursor(config, binding) == "next2"


@pytest.mark.parametrize("identifier", [0, -1, 2**64 - 1, 10**400 - 1])
def test_numeric_message_id_retains_original_and_replays(config, binding, identifier):
    event = message(identifier)
    response = poll(event)
    result = inbox.receive(config, binding, "", response)
    retained = rows(config, "events")[0]
    assert retained["event_id"] == str(identifier)
    assert "event_id" not in result["events"][0]
    assert json.loads(retained["raw"]) == event
    assert type(json.loads(retained["raw"])["message_id"]) is int
    inbox.initialize(config, binding)
    assert inbox.receive(config, binding, "", response) == result
    assert len(rows(config, "events")) == 1
    assert inbox.cursor(config, binding) == "next"


def test_numeric_and_string_id_conflict_preserves_original_and_cursor(config, binding):
    event = message(123)
    inbox.receive(config, binding, "", poll(event))
    with pytest.raises(IntegrityError):
        inbox.receive(config, binding, "next", poll(message("123"), cursor="changed"))
    assert json.loads(rows(config, "events")[0]["raw"]) == event
    assert len(rows(config, "events")) == 1
    assert inbox.cursor(config, binding) == "next"


@pytest.mark.parametrize("identifier", [True, False, 1.5, None, {}, [], 10**400, -(10**399)])
def test_invalid_message_id_never_advances_cursor(config, binding, identifier):
    with pytest.raises(InvalidInput):
        inbox.receive(config, binding, "", poll(message(identifier)))
    assert inbox.cursor(config, binding) == "" and not rows(config, "events")


@pytest.mark.parametrize(
    "change",
    [
        {"from_user_id": "intruder"},
        {"to_user_id": "different-bot"},
        {"room_id": "group"},
        {"chat_room_id": "group"},
        {"from_user_id": "synthetic-bot"},
    ],
)
def test_envelope_identity_only_ignores_unauthorized_without_body_retention(
    config, binding, change
):
    event = message(**change)
    event["forwarded_sender"] = "synthetic-owner"
    result = inbox.receive(config, binding, "", poll(event))
    assert result["ignored_messages"] == 1 and result["events"] == []
    assert rows(config, "events") == [] and inbox.cursor(config, binding) == "next"


def test_rebinding_and_revocation_refused(config, binding):
    changed = replace(binding, owner_id="different-owner")
    with pytest.raises(InvalidInput):
        inbox.initialize(config, changed)
    with pytest.raises(InvalidInput):
        inbox.receive(config, changed, "", poll(message()))
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=False, original=False, add=False)
    db.close()
    with pytest.raises(auth.Denied):
        inbox.receive(config, binding, "", poll(message()))
    assert rows(config, "bindings")[0]["cursor"] == "" and not rows(config, "events")


@pytest.mark.parametrize(
    "kind,state",
    [
        (2, "incomplete"),
        (3, "incomplete"),
        (4, "incomplete"),
        (5, "incomplete"),
        (99, "unsupported"),
    ],
)
def test_missing_media_unknown_types_stay_quarantined_and_raw_retained(
    config, binding, kind, state
):
    event = message(item_list=[{"type": kind}])
    result = inbox.receive(config, binding, "", poll(event))
    assert result["events"][0]["state"] == state and not result["dispatched"]
    assert json.loads(rows(config, "events")[0]["raw"]) == event
    assert inbox.cursor(config, binding) == "next"


def test_reference_media_preserved_and_nested_unknown_is_not_silently_ready(config, binding):
    event = message(
        item_list=[
            {"type": 1, "text_item": {"text": "引用"}, "ref_msg": {"message_item": {"type": 4}}}
        ]
    )
    result = inbox.receive(config, binding, "", poll(event), {"one": {"0.ref": b"original"}})
    assert result["events"][0]["state"] == "pending"
    event["message_id"] = "two"
    event["item_list"][0]["ref_msg"]["message_item"]["ref_msg"] = {"message_item": {"type": 1}}
    result = inbox.receive(
        config, binding, "next", poll(event, cursor="last"), {"two": {"0.ref": b"original"}}
    )
    assert result["events"][0]["state"] == "unsupported"


@pytest.mark.parametrize("change", ["raw", "media"])
def test_reused_event_id_cannot_change_data(config, binding, change):
    event = message(item_list=[{"type": 2}])
    response = poll(event)
    inbox.receive(config, binding, "", response, {"one": {"0": b"original"}})
    altered = copy.deepcopy(response)
    media = {"one": {"0": b"original"}}
    if change == "raw":
        altered["msgs"][0]["context_token"] = "different"
    else:
        media["one"]["0"] = b"changed"
    with pytest.raises(IntegrityError):
        inbox.receive(config, binding, "next", altered, media)
    assert rows(config, "media")[0]["data"] == b"original"


@pytest.mark.parametrize(
    "mutation", ["ret", "missing_id", "bad_items", "unexpected_media", "oversize"]
)
def test_invalid_poll_does_not_advance(config, binding, mutation):
    response = poll(message())
    media = {}
    if mutation == "ret":
        response["ret"] = 1
    elif mutation == "missing_id":
        del response["msgs"][0]["message_id"]
    elif mutation == "bad_items":
        response["msgs"][0]["item_list"] = "bad"
    elif mutation == "unexpected_media":
        media = {"one": {"0": b"unexpected"}}
    else:
        response["padding"] = "x" * (MAX_BYTES + 1)
    with pytest.raises(InvalidInput):
        inbox.receive(config, binding, "", response, media)
    assert inbox.cursor(config, binding) == "" and not rows(config, "events")


def test_second_event_conflict_rolls_back_entire_poll(config, binding):
    inbox.receive(config, binding, "", poll(message(), cursor="first"))
    changed = message()
    changed["item_list"][0]["text_item"]["text"] = "changed"
    with pytest.raises(IntegrityError):
        inbox.receive(config, binding, "first", poll(message("two"), changed))
    assert len(rows(config, "events")) == 1 and inbox.cursor(config, binding) == "first"


def test_disk_failure_before_commit_never_acknowledges_cursor(config, binding):
    transaction = database.transaction

    @contextmanager
    def full(db):
        with transaction(db):
            yield
            raise OSError(errno.ENOSPC, "synthetic disk full")

    with patch.object(database, "transaction", full), pytest.raises(OSError):
        inbox.receive(config, binding, "", poll(message()))
    assert inbox.cursor(config, binding) == "" and not rows(config, "events")
    assert inbox.receive(config, binding, "", poll(message()))["events"][0]["state"] == "pending"


def test_concurrent_same_poll_returns_one_durable_event(config, binding):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: inbox.receive(config, binding, "", poll(message())), range(2))
        )
    assert results[0] == results[1] and len(rows(config, "events")) == 1


def test_process_exit_during_cursor_update_rolls_back_bytes_and_event(config, binding, tmp_path):
    script = tmp_path / "crash.py"
    script.write_text("""import json,os,sys
from contextlib import contextmanager
from fiber_bundle.config import load_config
from fiber_bundle.weixin_events import Binding
from fiber_bundle import weixin_inbox as inbox
config=load_config(__import__('pathlib').Path(sys.argv[1])/'config.json',__import__('pathlib').Path(sys.argv[1]))
binding=Binding(**json.loads(sys.argv[2]))
original=inbox.connection
@contextmanager
def crash(*args,**kwargs):
    with original(*args,**kwargs) as db:
        db.create_function('crash_now',0,lambda:os._exit(73))
        db.execute("CREATE TEMP TRIGGER crash BEFORE UPDATE ON bindings "
                   "BEGIN SELECT crash_now(); END")
        yield db
inbox.connection=crash
inbox.receive(config,binding,'',json.loads(sys.argv[3]),{'one':{'0':b'persist then crash'}})
""")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(config.project_root),
            json.dumps(asdict(binding)),
            json.dumps(poll(message(item_list=[{"type": 2}]))),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert result.returncode == 73, result.stderr
    assert (
        inbox.cursor(config, binding) == ""
        and not rows(config, "events")
        and not rows(config, "media")
    )
    response = inbox.receive(
        config,
        binding,
        "",
        poll(message(item_list=[{"type": 2}])),
        {"one": {"0": b"persist then crash"}},
    )
    assert response["events"][0]["state"] == "pending"


def test_nonfinite_json_and_extra_media_rejected(binding):
    with pytest.raises(InvalidInput):
        prepare(binding, {"value": float("nan")}, {})
    with pytest.raises(InvalidInput):
        prepare(binding, poll(message()), {"unknown": {"0": b"bytes"}})
