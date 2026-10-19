"""Frozen prompts, literal authenticated commands and no-duplicate sending boundary."""

import asyncio
import json
import sqlite3

import pytest
from test_weixin_batches import at, capture
from test_weixin_inbox import binding as binding
from test_weixin_inbox import message, poll, rows

from fiber_bundle import auth, database
from fiber_bundle import weixin_batches as batches
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_prompt_delivery as delivery
from fiber_bundle.config import IntegrityError


@pytest.fixture
def prompt(config, binding):
    confirmations.initialize(config, binding)
    capture(config, binding, "one", 0)
    capture(config, binding, "two", 1)
    confirmations.process(config, binding)
    batch = batches.advance(config, binding, now=at(4))[0]
    return batch["batch_id"], confirmations.prepare(config, binding, batch["batch_id"])


def command(config, binding, value, event="cmd", **extra):
    response = poll(
        message(event, item_list=[{"type": 1, "text_item": {"text": value}}], **extra), cursor=event
    )
    result = inbox.receive(config, binding, inbox.cursor(config, binding), response)
    return result["events"][0]["job_id"] if result["events"] else None


@pytest.mark.parametrize("label,action", list(confirmations.ACTIONS.items()))
def test_four_exact_choices_and_commands_excluded_from_batches(
    config, binding, prompt, label, action
):
    batch_id, prepared = prompt
    job = command(config, binding, f"批次 {prepared['prompt_id']} {label}")
    confirmations.tick(config, binding)
    result = confirmations.receipt(config, binding, job)
    assert result["state"] == "accepted" and result["batch"]["action"] == action
    assert result["batch"]["dispatched"] is False
    assert len(rows(config, "wx_batch_members")) == 2
    assert confirmations.process(config, binding) == 0
    assert confirmations.prepare(config, binding, batch_id) == prepared


def test_question_followup_bound_to_same_batch(config, binding, prompt):
    batch_id, prepared = prompt
    command(config, binding, f"批次 {prepared['prompt_id']} 仅用于咨询")
    confirmations.process(config, binding)
    assert batches.get(config, binding, batch_id)["needs_question"]
    assert (
        "请补充咨询问题"
        in json.loads(rows(config, "wx_command_receipts")[-1]["result_json"])["reply_text"]
    )
    job = command(
        config, binding, f"批次 {prepared['prompt_id']} 仅用于咨询\n问题：比较两份资料", "followup"
    )
    confirmations.process(config, binding)
    assert not confirmations.receipt(config, binding, job)["batch"]["needs_question"]


@pytest.mark.parametrize(
    "variant", ["reference", "multi", "forwarded", "substring", "boolean_type"]
)
def test_evidence_text_cannot_trigger_choice(config, binding, prompt, variant):
    batch_id, prepared = prompt
    text = f"批次 {prepared['prompt_id']} 取消"
    msg = message("source", item_list=[{"type": 1, "text_item": {"text": text}}])
    if variant == "reference":
        msg["item_list"][0]["ref_msg"] = {
            "message_item": {"type": 1, "text_item": {"text": "source"}}
        }
    elif variant == "multi":
        msg["item_list"].append({"type": 1, "text_item": {"text": "source"}})
    elif variant == "forwarded":
        msg["forwarded"] = True
    elif variant == "boolean_type":
        msg["item_list"][0]["type"] = True
    else:
        msg["item_list"][0]["text_item"]["text"] = "原文示例：" + text
    inbox.receive(config, binding, inbox.cursor(config, binding), poll(msg, cursor="source"))
    confirmations.tick(config, binding)
    assert batches.get(config, binding, batch_id)["state"] == "frozen"
    assert len(rows(config, "wx_batch_members")) == 3


def test_other_sender_cannot_command(config, binding, prompt):
    batch_id, prepared = prompt
    assert (
        command(config, binding, f"批次 {prepared['prompt_id']} 取消", from_user_id="other") is None
    )
    confirmations.process(config, binding)
    assert batches.get(config, binding, batch_id)["state"] == "frozen"


def test_unknown_and_malformed_commands_are_rejected_not_archived(config, binding, prompt):
    for index, value in enumerate(["批次 0000000000000000 取消", "批次 unknown 随意操作"]):
        job = command(config, binding, value, str(index))
        confirmations.tick(config, binding)
        assert confirmations.receipt(config, binding, job)["state"] == "rejected"
    assert len(rows(config, "wx_batch_members")) == 2


def test_scope_tampering_rejects_command(config, binding, prompt):
    batch_id, prepared = prompt
    command(config, binding, f"批次 {prepared['prompt_id']} 取消")
    with inbox.connection(config, binding) as db:
        db.execute("DELETE FROM wx_batch_members WHERE batch_id=? AND ordinal=1", (batch_id,))
    with pytest.raises(IntegrityError, match="scope"):
        confirmations.process(config, binding)
    assert batches.get(config, binding, batch_id)["state"] == "frozen"


def test_command_and_choice_roll_back_together(config, binding, prompt):
    batch_id, prepared = prompt
    job = command(config, binding, f"批次 {prepared['prompt_id']} 取消")
    with inbox.connection(config, binding) as db:
        db.execute(
            "CREATE TRIGGER fail_command BEFORE INSERT ON wx_command_receipts "
            "WHEN NEW.kind='command' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        confirmations.process(config, binding)
    assert batches.get(config, binding, batch_id)["state"] == "frozen"
    with inbox.connection(config, binding) as db:
        db.execute("DROP TRIGGER fail_command")
    confirmations.process(config, binding)
    assert confirmations.receipt(config, binding, job)["state"] == "accepted"


def test_restart_creates_missing_prompt_and_does_not_reclassify_old_sources(config, binding):
    batches.initialize(config, binding)
    capture(
        config, binding, "old", 0, item_list=[{"type": 1, "text_item": {"text": "批次 old 取消"}}]
    )
    batch_id = batches.advance(config, binding, now=at(4))[0]["batch_id"]
    confirmations.initialize(config, binding)
    confirmations.tick(config, binding)
    prompts = confirmations.list_prompts(config, binding)["prompts"]
    assert len(prompts) == 1 and prompts[0]["batch_id"] == batch_id
    confirmations.tick(config, binding)
    assert len(confirmations.list_prompts(config, binding)["prompts"]) == 1
    assert rows(config, "wx_command_receipts")[0]["kind"] == "source"


@pytest.mark.parametrize("outcome", [True, False, "exception"])
def test_delivery_is_explicit_and_not_repeated(config, binding, prompt, outcome):
    _, prepared = prompt
    calls = []

    async def send(owner, body):
        assert owner == binding.owner_id and body == prepared["text"]
        calls.append(1)
        if outcome == "exception":
            raise OSError("private route must not leak")
        return outcome

    result = asyncio.run(delivery.deliver(config, binding, prepared["prompt_id"], send))
    assert result["state"] == ("sent" if outcome is True else "unknown")
    assert asyncio.run(delivery.deliver(config, binding, prepared["prompt_id"], send)) == result
    assert len(calls) == 1 and "private" not in json.dumps(result)


def test_concurrent_sender_and_cancelled_delivery_cannot_resend(config, binding, prompt):
    _, prepared = prompt

    async def scenario():
        entered = asyncio.Event()

        async def send(owner, body):
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(delivery.deliver(config, binding, prepared["prompt_id"], send))
        await entered.wait()
        assert (await delivery.deliver(config, binding, prepared["prompt_id"], send))[
            "state"
        ] == "sending"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert confirmations.list_prompts(config, binding)["prompts"][0]["state"] == "sending"


def test_revocation_blocks_processing_and_delivery(config, binding, prompt):
    db = database.connect(config.state_root)
    auth.set_grant(db, binding.client_id, binding.vault_id, query=False, original=False, add=False)
    db.close()
    with pytest.raises(auth.Denied):
        confirmations.process(config, binding)

    async def forbidden(*args):
        pytest.fail("revoked sender")

    with pytest.raises(auth.Denied):
        asyncio.run(delivery.deliver(config, binding, prompt[1]["prompt_id"], forbidden))


def test_partial_classification_cannot_freeze_a_partial_burst(config, binding):
    confirmations.initialize(config, binding)
    capture(config, binding, "first", 0)
    capture(config, binding, "second", 1)
    assert confirmations.process(config, binding, limit=1) == 1
    assert batches.advance(config, binding, now=at(10)) == []
    assert rows(config, "wx_batches") == []
    confirmations.process(config, binding)
    view = batches.advance(config, binding, now=at(10))[0]
    assert view["state"] == "frozen" and len(view["members"]) == 2
