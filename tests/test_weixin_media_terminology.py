import asyncio

import pytest
from test_media_glossary import SNAPSHOT, freeze
from test_media_jobs import create
from test_media_jobs import media_scope as media_scope
from test_weixin_confirmations import command

from fiber_bundle import auth, media_glossary, media_jobs
from fiber_bundle import weixin_confirmations as confirmations
from fiber_bundle import weixin_inbox as inbox
from fiber_bundle import weixin_media_terminology as terminology
from fiber_bundle import weixin_terminology_delivery as delivery
from fiber_bundle.config import InvalidInput
from fiber_bundle.media_files import canonical
from fiber_bundle.weixin_delivery_state import ensure_resolved
from fiber_bundle.weixin_events import Binding


def prompt(config, scope, snapshot=SNAPSHOT):
    job = create(config, scope)
    freeze(config, scope, job, canonical(snapshot))
    with media_jobs.connection(config) as db:
        auth.set_grant(db, scope[0]["id"], scope[1], add=True, query=True, original=False)
    binding = Binding("synthetic-bot", "synthetic-owner", scope[0]["id"], scope[1])
    inbox.initialize(config, binding)
    confirmations.initialize(config, binding)
    p = terminology.prepare(config, binding, job)
    with inbox.connection(config, binding) as db:
        row = db.execute(
            "SELECT * FROM wx_media_terminology_prompts WHERE id=?", (p["prompt_id"],)
        ).fetchone()
    label = "确认" if snapshot["terms"] else "确认空表"
    text = f"术语 {p['prompt_id']} {label} {row['revision']} {row['sha256']}"
    return job, binding, p, text


def test_exact_owner_confirmation_deduplicates_without_dispatch(config, media_scope):
    job, b, p, text = prompt(config, media_scope)
    assert terminology.prepare(config, b, job) == p
    receipt = command(config, b, text)
    assert confirmations.process(config, b) == 1
    assert confirmations.receipt(config, b, receipt)["state"] == "accepted"
    assert media_glossary.status(config, b.client_id, job)["terminology_gate_passed"]
    assert confirmations.process(config, b) == 0
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_processing").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0


@pytest.mark.parametrize(
    "case", ["stale", "forwarded", "foreign", "bad_sha", "ambiguous", "cancelled"]
)
def test_stale_or_evidence_commands_cannot_confirm(config, media_scope, case):
    job, b, p, text = prompt(config, media_scope)
    if case == "stale":
        freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": "changed"}))
    if case == "ambiguous":
        freeze(
            config,
            media_scope,
            job,
            canonical({**SNAPSHOT, "unresolved": [{"term": "unknown", "reason": "uncertain"}]}),
        )
    if case == "cancelled":
        media_jobs.stop(config, b.client_id, job)
    if case == "bad_sha":
        text = text[:-64] + "a" * 64
    extra = (
        {"forwarded": True}
        if case == "forwarded"
        else {"from_user_id": "other"}
        if case == "foreign"
        else {}
    )
    receipt = command(config, b, text, **extra)
    confirmations.process(config, b)
    if receipt is not None and case != "forwarded":
        assert confirmations.receipt(config, b, receipt)["state"] == "rejected"
    with media_jobs.connection(config) as db:
        assert db.execute("SELECT count(*) FROM media_glossary_confirmations").fetchone()[0] == 0


@pytest.mark.parametrize("ack", [True, False])
def test_notification_once_and_unknown_blocks_account(config, media_scope, ack):
    job, b, p, text = prompt(config, media_scope)
    sent = []

    async def sender(owner, body, authorize):
        authorize()
        assert owner == b.owner_id and text in body
        sent.append(body)
        return ack

    first = asyncio.run(delivery.deliver(config, b, p["prompt_id"], sender))
    second = asyncio.run(delivery.deliver(config, b, p["prompt_id"], sender))
    assert first == second and len(sent) == 1 and first["state"] == ("sent" if ack else "unknown")
    if not ack:
        with inbox.connection(config, b) as db:
            with pytest.raises(InvalidInput, match="unresolved"):
                ensure_resolved(db, b)


def test_query_revocation_stops_notification_and_confirmation(config, media_scope):
    job, b, p, text = prompt(config, media_scope)
    with media_jobs.connection(config) as db:
        auth.set_grant(db, b.client_id, b.vault_id, add=True, query=False, original=False)

    async def sender(*args):
        raise AssertionError("sender must not be reached")

    with pytest.raises(auth.Denied):
        asyncio.run(delivery.deliver(config, b, p["prompt_id"], sender))
    receipt = command(config, b, text)
    confirmations.process(config, b)
    assert confirmations.receipt(config, b, receipt)["state"] == "rejected"


def test_send_gate_rechecks_query_permission_without_export(config, media_scope):
    job, b, p, text = prompt(config, media_scope)

    async def sender(owner, body, authorize):
        with media_jobs.connection(config) as db:
            auth.set_grant(db, b.client_id, b.vault_id, add=True, query=False, original=False)
        authorize()
        raise AssertionError("permission revoked before external send")

    result = asyncio.run(delivery.deliver(config, b, p["prompt_id"], sender))
    assert result["state"] == "unknown"


@pytest.mark.parametrize("case", ["confirmed", "stale"])
def test_unsent_stale_prompt_is_retired_without_notification(config, media_scope, case):
    job, b, p, text = prompt(config, media_scope)
    if case == "confirmed":
        command(config, b, text)
        confirmations.process(config, b)
    else:
        freeze(config, media_scope, job, canonical({**SNAPSHOT, "topic": "new"}))

    async def sender(*args):
        raise AssertionError("obsolete prompt cannot send")

    assert asyncio.run(delivery.deliver(config, b, p["prompt_id"], sender))["state"] == "obsolete"
