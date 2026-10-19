import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import fiber_bundle
from fiber_bundle.config import InvalidInput
from fiber_bundle.weixin_events import MAX_BYTES, Binding, prepare
from fiber_bundle.weixin_payload_preflight import inspect
from fiber_bundle.weixin_payload_preflight_cli import read_sample

PRIVATE = "private-sentinel-do-not-output"


def text_item():
    return {"type": 1, "text_item": {"text": PRIVATE}}


def poll(items=None):
    return {
        "get_updates_buf": PRIVATE,
        "msgs": [
            {
                "message_id": PRIVATE,
                "from_user_id": "owner-private",
                "to_user_id": "account-private",
                "item_list": [text_item()] if items is None else items,
                "nickname": PRIVATE,
                "context_token": PRIVATE,
            }
        ],
    }


def encoded(value):
    return json.dumps(value).encode()


def assert_private(view):
    rendered = json.dumps(view)
    assert PRIVATE not in rendered and "owner-private" not in rendered
    assert "account-private" not in rendered and "context_token" not in rendered
    assert view["publication_ready"] is False and view["knowledge_verified"] is False
    assert view["platform_completeness"] == view["authorization"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "items",
    [
        [text_item()],
        [{"type": 2, "image_item": {"cdn": PRIVATE}}],
        [{"type": k} for k in (2, 3, 4, 5)],
        [{**text_item(), "ref_msg": {"message_item": text_item()}}],
        [{**text_item(), "ref_msg": {"message_item": {"type": 4}}}],
        [{**text_item(), "ref_msg": {"message_item": {"type": 99}}}],
        [{**text_item(), "ref_msg": {"message_item": {**text_item(), "ref_msg": {}}}}],
        [{"type": 99, "merged_records": [PRIVATE]}],
        [{"type": True}],
        [{"type": [1]}],
        [{"type": 1, "text_item": {"text": " "}}],
        [{"type": 1, "text_item": {"text": None}}],
        [None],
        [],
    ],
)
def test_preflight_classification_matches_existing_receiver_without_authorizing(items):
    value = poll(items)
    source = encoded(value)
    view = inspect(source, "ilink-poll")
    binding = Binding("account-private", "owner-private", "synthetic-client", "synthetic-vault")
    _, _, events, _ = prepare(binding, value, {})
    assert view["messages"][0]["receiver_state_without_media"] == events[0].state
    assert view["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert view["source_bytes"] == len(source)
    assert_private(view)


def test_references_locate_original_order_without_claiming_merged_source_coverage():
    view = inspect(
        encoded(poll([{**text_item(), "ref_msg": {"message_item": {"type": 4}}}])),
        "ilink-poll",
    )
    assert [v["item_key"] for v in view["messages"][0]["item_locations"]] == ["0", "0.ref"]
    assert view["messages"][0]["required_media_keys"] == ["0.ref"]
    assert view["expected_source_count"] is None
    assert view["state"] == "incomplete"
    assert "supply_media_in_existing_explicit_recovery_flow" in view["next_actions"]


def test_duplicate_ids_report_conflict_without_echoing_ids_or_deduplicating():
    value = poll()
    value["msgs"] *= 2
    view = inspect(encoded(value), "ilink-poll")
    assert view["state"] == "conflict" and view["duplicate_message_ids"] == 1
    assert view["message_count"] == 2
    assert_private(view)


def test_numeric_id_matches_receiver_and_detects_string_identity_conflict():
    value = poll()
    value["msgs"][0]["message_id"] = 2**64 - 1
    source = encoded(value)
    view = inspect(source, "ilink-poll")
    binding = Binding("account-private", "owner-private", "synthetic-client", "synthetic-vault")
    _, _, events, _ = prepare(binding, value, {})
    assert view["messages"][0]["receiver_state_without_media"] == events[0].state
    assert view["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert_private(view)
    value["msgs"].append({**value["msgs"][0], "message_id": str(2**64 - 1)})
    view = inspect(encoded(value), "ilink-poll")
    assert view["state"] == "conflict" and view["duplicate_message_ids"] == 1


@pytest.mark.parametrize("identifier", [True, False, 1.5, None, {}, [], 10**400])
def test_preflight_rejects_invalid_message_ids(identifier):
    value = poll()
    value["msgs"][0]["message_id"] = identifier
    with pytest.raises(InvalidInput, match="payload_message_id"):
        inspect(encoded(value), "ilink-poll")


def test_group_and_missing_identity_are_observations_not_grants():
    value = poll()
    value["msgs"][0].update(room_id=PRIVATE, from_user_id=None)
    view = inspect(encoded(value), "ilink-poll")
    assert view["messages"][0]["group_marker_present"]
    assert not view["messages"][0]["identity_fields_present"]
    assert_private(view)


@pytest.mark.parametrize("count,state", [(1, "inspected"), (2, "incomplete"), (None, "incomplete")])
def test_normalized_unknown_fields_and_missing_count_remain_visible(count, state):
    source = encoded(
        {
            "schema": "fiber-chat-source/v1",
            "expected_count": count,
            "messages": [
                {"text": PRIVATE, "sender": None, "sent_at": None, "source_message_id": None}
            ],
        }
    )
    view = inspect(source, "normalized-chat")
    assert view["messages"] == [
        {"message_index": 1, "text_bytes": len(PRIVATE), "unknown_source_fields": 3}
    ]
    assert view["state"] == state and view["expected_source_count"] == count
    assert_private(view)


@pytest.mark.parametrize(
    "source,kind",
    [
        (b"", "ilink-poll"),
        (b"x" * (MAX_BYTES + 1), "ilink-poll"),
        (b"{", "ilink-poll"),
        (b"\xff", "ilink-poll"),
        (b'{"private-sentinel-do-not-output":1,"private-sentinel-do-not-output":2}', "ilink-poll"),
        (b'{"msgs":NaN}', "ilink-poll"),
        (b'{"msgs":Infinity}', "ilink-poll"),
        (b"[" * 2000 + b"]" * 2000, "ilink-poll"),
        (encoded(poll()), "guess"),
        (encoded(poll()), "normalized-chat"),
        (b'{"schema":"fiber-chat-source/v1","messages":[]}', "ilink-poll"),
        (encoded({**poll(), "ret": True}), "ilink-poll"),
        (encoded({**poll(), "errcode": 1}), "ilink-poll"),
        (encoded({**poll(), "get_updates_buf": None}), "ilink-poll"),
        (encoded({**poll(), "msgs": [None]}), "ilink-poll"),
        (encoded({**poll(), "msgs": poll()["msgs"] * 101}), "ilink-poll"),
        (encoded(poll([text_item()] * 101)), "ilink-poll"),
    ],
)
def test_bad_shapes_and_unbounded_inputs_refused_without_private_diagnostics(source, kind):
    with pytest.raises(InvalidInput) as caught:
        inspect(source, kind)
    assert PRIVATE not in str(caught.value)


def test_cli_readonly_and_redacted_success_and_failure(tmp_path):
    root = tmp_path.resolve()
    sample = root / (PRIVATE + ".json")
    sample.write_bytes(encoded(poll()))
    (root / "metadata.sqlite3").write_bytes(b"must remain untouched")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    source_root = Path(fiber_bundle.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(source_root), "PYTHONDONTWRITEBYTECODE": "1"}

    def run():
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "fiber_bundle.weixin_payload_preflight_cli",
                "--payload",
                str(sample),
                "--input-kind",
                "ilink-poll",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    process = run()
    assert process.returncode == 0 and not process.stderr
    assert_private(json.loads(process.stdout))
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    sample.write_bytes(b'{"private-sentinel-do-not-output":')
    process = run()
    assert process.returncode == 1 and not process.stderr
    assert json.loads(process.stdout) == {"state": "rejected", "reason": "payload_preflight_failed"}
    assert PRIVATE not in process.stdout


@pytest.mark.parametrize("kind", ["empty", "directory", "symlink", "parent-symlink", "fifo"])
def test_read_sample_refuses_nonregular_or_redirected_file(tmp_path, kind):
    root = tmp_path.resolve()
    sample = root / "sample.json"
    if kind == "empty":
        sample.touch()
    elif kind == "directory":
        sample.mkdir()
    elif kind == "fifo":
        os.mkfifo(sample)
    elif kind == "symlink":
        sample.symlink_to(root / "missing")
    else:
        target = root / "target"
        target.mkdir()
        (target / "sample.json").write_bytes(encoded(poll()))
        (root / "link").symlink_to(target, target_is_directory=True)
        sample = root / "link/sample.json"
    with pytest.raises((InvalidInput, OSError)):
        read_sample(sample)
