import json
from dataclasses import replace

import pytest
from test_document_creation_config import creation_policy as creation_policy
from test_document_creation_execution import claim
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_rounds import finish_rounds, output_file
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth, database, execution_ledger, search
from fiber_bundle import document_creation_artifacts as artifacts
from fiber_bundle import document_creation_binding as binding
from fiber_bundle import document_creation_continuation as continuation
from fiber_bundle import document_creation_execution as execution
from fiber_bundle import document_creation_profile as profile
from fiber_bundle import document_creation_rounds as rounds
from fiber_bundle import document_creation_save as save
from fiber_bundle import document_creation_tasks as tasks
from fiber_bundle.config import InvalidInput
from fiber_bundle.document_creation_config import parse
from fiber_bundle.objects import digest


@pytest.fixture
def attached(config, owner, prepared_task, creation_policy):
    creation_policy["model_config"].update(
        image=profile.IMAGE,
        max_calls=2,
        daily_calls=8,
        max_input_tokens=131072,
        max_request_bytes=131072,
        task_limit_nusd=100000000,
        daily_limit_nusd=200000000,
    )
    creation_policy["capability_revision"] = profile.REVISION
    policy = parse(creation_policy)
    with execution.responsibility(config, owner[0], prepared_task["task_id"]) as guard:
        db = database.connect(config.state_root)
        try:
            with database.locked(config):
                attempt = claim(config, db, owner[0], prepared_task, policy, guard)
                rounds.attach(config, db, attempt, policy, guard)
            yield db, policy, guard, attempt
        finally:
            db.close()


@pytest.fixture
def completed(config, attached, tmp_path):
    db, policy, guard, attempt = attached
    finish_rounds(config, attached)
    raw = output_file(config, attached, tmp_path)
    metadata = artifacts.register(config, db, attempt, policy, guard, tmp_path)
    return db, policy, attempt, raw, metadata


def enqueue(config, db, attempt, addition="Add a summary", **kwargs):
    parent = tasks.status(config, db, attempt.client_id, attempt.task_id)
    return continuation.enqueue(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        "append-one",
        addition,
        expected_revision=parent["revision"],
        expected_sha256=parent["plan_sha256"],
        **kwargs,
    )


def test_running_addition_is_frozen_child_not_live_prompt_and_only_released_after_finish(
    config, attached, tmp_path
):
    db, policy, guard, attempt = attached
    parent = binding.get(db, attempt.task_id)
    queued = enqueue(config, db, attempt)
    child = queued["child"]["task_id"]
    assert queued["state"] == "queued"
    assert continuation.previous(config, db, attempt.client_id, attempt.task_id) is None
    before = list(db.iterdump())
    with execution.responsibility(config, attempt.client_id, child) as next_owner:
        with pytest.raises(InvalidInput, match="not_ready"):
            execution.claim(
                config,
                db,
                attempt.client_id,
                child,
                policy,
                next_owner,
                expected_revision=queued["child"]["revision"],
                expected_plan_sha256=queued["child"]["plan_sha256"],
                expected_configuration_sha256=policy.sha256,
                allow_reservation=True,
            )
    assert list(db.iterdump()) == before
    assert binding.get(db, attempt.task_id)["request_json"] == parent["request_json"]
    finish_rounds(config, attached)
    raw = output_file(config, attached, tmp_path)
    artifacts.register(config, db, attempt, policy, guard, tmp_path)
    ready = continuation.release(config, db, attempt.client_id, attempt.task_id)
    assert ready["state"] == "ready"
    assert continuation.previous(config, db, attempt.client_id, child).encode() == raw
    assert enqueue(config, db, attempt) == ready
    with pytest.raises(InvalidInput, match="continuation_exists"):
        enqueue(config, db, attempt, "Different requirement")
    history = continuation.versions(config, db, attempt.client_id, attempt.task_id)
    assert [v["document_version"] for v in history] == [1, 2]
    assert continuation.versions(config, db, attempt.client_id, child)[0]["document_version"] == 2
    assert history[0]["revision"] == 1 and history[1]["revision"] == 2


def test_versions_share_task_money_and_call_ceiling(config, completed):
    db, policy, attempt, _, _ = completed
    result = enqueue(config, db, attempt)
    child = result["child"]
    with execution.responsibility(config, attempt.client_id, child["task_id"]) as owner:
        with pytest.raises(execution_ledger.BudgetExceeded, match="version_budget"):
            execution.claim(
                config,
                db,
                attempt.client_id,
                child["task_id"],
                policy,
                owner,
                expected_revision=child["revision"],
                expected_plan_sha256=child["plan_sha256"],
                expected_configuration_sha256=policy.sha256,
                allow_reservation=True,
            )
        approved = replace(policy, model_config=replace(policy.policy(), max_calls=4))
        claimed = execution.claim(
            config,
            db,
            attempt.client_id,
            child["task_id"],
            approved,
            owner,
            expected_revision=child["revision"],
            expected_plan_sha256=child["plan_sha256"],
            expected_configuration_sha256=approved.sha256,
            allow_reservation=True,
        )
        wire = json.loads(binding.get(db, claimed.task_id)["request_json"])
        assert "previous_unverified_draft" in json.loads(wire["messages"][1]["content"])
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 3


def test_multitask_ambiguity_never_picks_latest_and_other_owner_cannot_select(config, completed):
    db, _, attempt, _, _ = completed
    enqueue(config, db, attempt)
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    result = continuation.candidates(config, db, attempt.client_id, plan.query_task_id)
    assert result["state"] == "confirmation_required" and len(result["candidates"]) == 2
    assert (
        continuation.candidates(config, db, attempt.client_id, plan.query_task_id, attempt.task_id)[
            "state"
        ]
        == "selected"
    )
    other = auth.create_client(db, "other", False)
    with pytest.raises(auth.Denied):
        continuation.candidates(config, db, other["id"], plan.query_task_id, attempt.task_id)


def test_input_update_requires_exact_preview_and_preserves_original(config, completed, routing):
    db, _, attempt, _, _ = completed
    parent = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    from fiber_bundle import local_query_tasks
    from fiber_bundle.answer_tasks import AnswerInput
    from fiber_bundle.query_routing_config import parse

    source = db.execute(
        "SELECT * FROM local_query_tasks WHERE id=?", (parent.query_task_id,)
    ).fetchone()
    request = json.loads(source["request_json"])
    # Freeze another authorized query, distinct from the original input binding.
    data = AnswerInput(
        request["receipt_id"] if "receipt_id" in request else source["receipt_id"],
        request["session_id"],
        "What pressure?",
        "pressure",
        submission_id="new-input",
    )
    # The fixture has the configured routing object in its stored task policy.
    new = local_query_tasks.submit(config, db, parse(routing), attempt.client_id, data)
    preview = continuation.update_preview(
        config, db, attempt.client_id, attempt.task_id, new["task_id"]
    )
    with pytest.raises(InvalidInput, match="confirmation_required"):
        enqueue(config, db, attempt, updated_query_task_id=new["task_id"])
    result = enqueue(
        config,
        db,
        attempt,
        updated_query_task_id=new["task_id"],
        expected_update_sha256=preview["preview_sha256"],
    )
    child = tasks.plan(config, db, attempt.client_id, result["child"]["task_id"])
    assert child.query_task_id == new["task_id"] and child.input_json != parent.input_json
    assert tasks.plan(config, db, attempt.client_id, attempt.task_id) == parent


def test_explicit_save_and_provenance_is_searchable_as_analysis_not_evidence(config, completed):
    db, _, attempt, raw, meta = completed
    vault_id = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    before = list(db.iterdump())
    with pytest.raises(InvalidInput, match="not_requested"):
        save.prepare(
            config,
            db,
            attempt.client_id,
            attempt.task_id,
            vault_id,
            expected_artifact_sha256=digest(raw),
        )
    assert list(db.iterdump()) == before
    prepared = save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault_id,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    assert prepared["state"] == "prepared"
    result = save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    assert result["state"] == "published" and result["knowledge_verified"] is False
    matches = search.search(config, db, attempt.client_id, "100 psi", [vault_id])
    assert any(
        v["source_kind"] == "agent_analysis" and "非独立证据" in v["source_label"]
        for v in matches["results"]
    )
    root = config.project_root / dict(config.vaults)["personal"] / result["relative_path"]
    assert (root / meta["artifact"]["original_name"]).read_bytes() == raw
    provenance = json.loads((root / "provenance.json").read_bytes())
    assert provenance["draft_version"] == meta and provenance["source_snapshot"]["sources"]
    (root / "note.md").write_bytes(b"human modification")
    assert save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)[
        "historical"
    ]
    assert (root / "note.md").read_bytes() == b"human modification"


def test_save_scope_expansion_requires_admin_and_new_reader_grants_do_not_expand(config, completed):
    db, _, attempt, raw, _ = completed
    supplier = db.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    auth.set_grant(db, attempt.client_id, supplier, query=True, original=False, add=True)
    other = auth.create_client(db, "supplier-reader", False)
    auth.set_grant(db, other["id"], supplier, query=True, original=False, add=False)
    prepared = save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        supplier,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    assert prepared["state"] == "scope_approval_required"
    with pytest.raises(InvalidInput, match="approval_required"):
        save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    with pytest.raises(auth.Denied):
        save.approve(db, prepared["run_id"], prepared["intent_sha256"], admin_cookie="", csrf="")
    auth.set_password(config, "a-local-synthetic-password")
    cookie, csrf, _ = auth.login(db, "a-local-synthetic-password", 600)
    save.approve(db, prepared["run_id"], prepared["intent_sha256"], admin_cookie=cookie, csrf=csrf)
    result = save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    assert search.search(config, db, other["id"], "100 psi", [supplier])["results"]
    newcomer = auth.create_client(db, "newcomer", False)
    auth.set_grant(db, newcomer["id"], supplier, query=True, original=False, add=False)
    assert not search.search(config, db, newcomer["id"], "100 psi", [supplier])["results"]
    assert result["state"] == "published"


@pytest.fixture
def channel(config, completed):
    from fiber_bundle import weixin_inbox
    from fiber_bundle.weixin_events import Binding

    db, _, attempt, _, _ = completed
    vault_id = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    owner = Binding("synthetic-account", "synthetic-owner", attempt.client_id, vault_id)
    weixin_inbox.initialize(config, owner)
    return owner


@pytest.mark.parametrize("outcome", ["sent", "failed", "unknown", "no_guard", "duplicate"])
def test_file_delivery_claim_and_failure_notice_never_retry_or_generate(
    config, completed, channel, outcome
):
    import asyncio
    from unittest.mock import patch

    from fiber_bundle import document_creation_delivery as delivery

    db, _, attempt, raw, _ = completed
    calls, notices = [], []

    async def sender(owner, asset, guard):
        calls.append((owner, asset.data))
        if outcome == "no_guard":
            return True
        guard("allocate")
        if outcome == "duplicate":
            guard("allocate")
        if outcome == "unknown":
            raise OSError("provider body must not be saved")
        guard("upload")
        guard("send")
        return outcome == "sent"

    async def notice(owner, message, guard):
        guard()
        notices.append((owner, message))
        return True

    with patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("regenerate")):
        result = asyncio.run(
            delivery.deliver(
                config,
                channel,
                attempt.task_id,
                sender,
                notice,
                expected_artifact_sha256=digest(raw),
                max_bytes=len(raw),
                allow_delivery=True,
            )
        )
        assert result["state"] == (outcome if outcome in {"sent", "failed"} else "unknown")
        assert result["local_retained"] and len(calls) == 1
        assert len(notices) == (0 if outcome == "sent" else 1)
        assert (
            asyncio.run(
                delivery.deliver(
                    config,
                    channel,
                    attempt.task_id,
                    sender,
                    notice,
                    expected_artifact_sha256=digest(raw),
                    max_bytes=len(raw),
                    allow_delivery=True,
                )
            )
            == result
        )
    assert (
        len(calls) == 1 and artifacts.get(config, db, attempt.client_id, attempt.task_id)[0] == raw
    )
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2
    row = db.execute("SELECT * FROM document_creation_deliveries").fetchone()
    assert "provider body" not in row["intent_json"]


@pytest.mark.parametrize("mutation", ["cancel", "revoke", "source"])
def test_each_file_transport_stage_rechecks_authority(config, completed, channel, mutation):
    import asyncio

    from fiber_bundle import document_creation_delivery as delivery

    db, _, attempt, raw, _ = completed
    sent = []

    async def sender(owner, asset, guard):
        guard("allocate")
        if mutation == "cancel":
            current = tasks.status(config, db, attempt.client_id, attempt.task_id)
            tasks.cancel(
                config,
                db,
                attempt.client_id,
                attempt.task_id,
                expected_revision=current["revision"],
                expected_sha256=current["plan_sha256"],
            )
        elif mutation == "revoke":
            db.execute(
                "UPDATE client_vault_grants SET can_query=0 WHERE client_id=?", (attempt.client_id,)
            )
        else:
            db.execute("UPDATE local_query_tasks SET snapshot_json='[]'")
        guard("upload")
        sent.append(asset)
        return True

    result = asyncio.run(
        delivery.deliver(
            config,
            channel,
            attempt.task_id,
            sender,
            expected_artifact_sha256=digest(raw),
            max_bytes=len(raw),
            allow_delivery=True,
        )
    )
    assert result["state"] == "unknown" and not sent
    assert result["notice_state"] == "unavailable"
    assert (config.state_root / "document-drafts" / attempt.run_id / "draft.md").read_bytes() == raw


def test_delivery_default_off_upload_bound_wrong_recipient_and_cancelled_claim(
    config, completed, channel
):
    import asyncio
    from dataclasses import replace

    from fiber_bundle import document_creation_delivery as delivery

    db, _, attempt, raw, _ = completed

    async def never(*args):
        raise AssertionError("sender")

    with pytest.raises(InvalidInput, match="not_enabled"):
        asyncio.run(
            delivery.deliver(
                config,
                channel,
                attempt.task_id,
                never,
                expected_artifact_sha256=digest(raw),
                max_bytes=len(raw),
            )
        )
    with pytest.raises(InvalidInput, match="limit_changed"):
        asyncio.run(
            delivery.deliver(
                config,
                channel,
                attempt.task_id,
                never,
                expected_artifact_sha256=digest(raw),
                max_bytes=len(raw) - 1,
                allow_delivery=True,
            )
        )
    with pytest.raises(InvalidInput, match="binding mismatch"):
        asyncio.run(
            delivery.deliver(
                config,
                replace(channel, owner_id="foreign"),
                attempt.task_id,
                never,
                expected_artifact_sha256=digest(raw),
                max_bytes=len(raw),
                allow_delivery=True,
            )
        )
    assert not db.execute("SELECT * FROM document_creation_deliveries").fetchall()

    async def interrupted(owner, asset, guard):
        guard("allocate")
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            delivery.deliver(
                config,
                channel,
                attempt.task_id,
                interrupted,
                expected_artifact_sha256=digest(raw),
                max_bytes=len(raw),
                allow_delivery=True,
            )
        )
    assert delivery.status(db, attempt.client_id, attempt.task_id)["state"] == "sending"
    assert delivery.reconcile(db, attempt.client_id, attempt.task_id)["state"] == "unknown"
    assert (
        asyncio.run(
            delivery.deliver(
                config,
                channel,
                attempt.task_id,
                never,
                expected_artifact_sha256=digest(raw),
                max_bytes=len(raw),
                allow_delivery=True,
            )
        )["state"]
        == "unknown"
    )


def test_save_rename_interruption_reconciles_without_overwrite_or_new_version(
    config, completed, monkeypatch
):
    from fiber_bundle import publication_files

    db, _, attempt, raw, _ = completed
    vault = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    original = publication_files.rename_exclusive

    def interrupted(*args):
        original(*args)
        raise OSError("after rename")

    monkeypatch.setattr(publication_files, "rename_exclusive", interrupted)
    with pytest.raises(OSError, match="after rename"):
        save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    before = db.execute("SELECT count(*) FROM source_versions").fetchone()[0]
    monkeypatch.setattr(publication_files, "rename_exclusive", original)
    result = save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    assert result["state"] == "published"
    assert db.execute("SELECT count(*) FROM source_versions").fetchone()[0] == before


def test_save_partial_stage_preserved_and_not_overwritten(config, completed, monkeypatch):
    from fiber_bundle import publication_files
    from fiber_bundle.config import IntegrityError

    db, _, attempt, raw, _ = completed
    vault = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    original = publication_files.stage

    def partial(root, relative, files):
        path = root / relative
        path.mkdir()
        (path / "note.md").write_bytes(b"partial")
        raise OSError("partial")

    monkeypatch.setattr(publication_files, "stage", partial)
    with pytest.raises(OSError, match="partial"):
        save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    monkeypatch.setattr(publication_files, "stage", original)
    with pytest.raises(IntegrityError):
        save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    row = db.execute("SELECT intent_json FROM document_creation_saves").fetchone()
    stage = (
        config.project_root / dict(config.vaults)["personal"] / json.loads(row[0])["staging_path"]
    )
    assert (stage / "note.md").read_bytes() == b"partial"


def test_authenticated_http_draft_append_candidates_save_and_chain_cancel(
    config, completed, http, owner
):
    db, _, attempt, raw, _ = completed
    url = "/v1/document-tasks/" + attempt.task_id
    assert http.get(url + "/draft").status_code == 401
    response = http.get(url + "/draft", headers=owner[1])
    assert response.status_code == 200 and response.content == raw
    current = tasks.status(config, db, attempt.client_id, attempt.task_id)
    append = http.post(
        url + "/append",
        headers=owner[1],
        json={
            "amendment_id": "http-add",
            "addition": "Add an executive summary",
            "expected_revision": current["revision"],
            "expected_sha256": current["plan_sha256"],
        },
    )
    assert append.status_code == 200, append.text
    versions = http.get(url + "/versions", headers=owner[1]).json()
    assert [v["document_version"] for v in versions] == [1, 2]
    query = tasks.plan(config, db, attempt.client_id, attempt.task_id).query_task_id
    assert (
        http.get("/v1/document-candidates/" + query, headers=owner[1]).json()["state"]
        == "confirmation_required"
    )
    payload = {"target_vault_id": owner[2]["personal"], "expected_artifact_sha256": digest(raw)}
    assert http.post(url + "/save", headers=owner[1], json=payload).status_code == 422
    assert (
        http.post(
            url + "/save", headers=owner[1], json={**payload, "save_analysis": True}
        ).status_code
        == 200
    )
    cancelled = http.post(
        url + "/cancel",
        headers=owner[1],
        json={
            "expected_revision": current["revision"],
            "expected_sha256": current["plan_sha256"],
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert all(
        v["state"] == "cancelled" for v in http.get(url + "/versions", headers=owner[1]).json()
    )
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2


def test_corrupted_save_paths_block_even_rehashed_intent(config, completed):
    from fiber_bundle.config import IntegrityError
    from fiber_bundle.inputs import canonical

    db, _, attempt, raw, _ = completed
    vault = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    intent = json.loads(db.execute("SELECT intent_json FROM document_creation_saves").fetchone()[0])
    intent["relative_path"] = "../../outside"
    body = canonical(intent)
    db.execute(
        "UPDATE document_creation_saves SET intent_json=?, intent_sha256=?",
        (body, digest(body.encode())),
    )
    with pytest.raises(IntegrityError, match="intent_changed"):
        save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    assert db.execute("SELECT count(*) FROM source_versions").fetchone()[0] == 1


def test_owner_weixin_commands_queue_and_revise_without_model_or_file_dispatch(
    config, completed, channel
):
    from unittest.mock import patch

    from test_weixin_confirmations import command

    from fiber_bundle import weixin_confirmations as confirmations
    from fiber_bundle import weixin_document_commands as commands
    from fiber_bundle import weixin_inbox as inbox

    db, _, attempt, _, _ = completed
    confirmations.initialize(config, channel)
    current = tasks.status(config, db, attempt.client_id, attempt.task_id)
    text = (
        f"文档 续改 {attempt.task_id} {current['revision']} {current['plan_sha256']}"
        "\nAdd a conclusion"
    )
    with patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")):
        event = command(config, channel, text, to_user_id=channel.account_id)
        assert confirmations.process(config, channel) == 1
        result = confirmations.receipt(config, channel, event)
        assert result["state"] == "accepted" and result["document"]["state"] == "ready"
        assert confirmations.process(config, channel) == 0
        assert len(continuation.versions(config, db, channel.client_id, attempt.task_id)) == 2
        with inbox.connection(config, channel):
            candidate = commands.apply_locked(
                config,
                channel,
                "candidate",
                "文档 候选 "
                + tasks.plan(config, db, channel.client_id, attempt.task_id).query_task_id,
            )
        assert candidate["document"]["state"] == "confirmation_required"
        foreign = command(
            config,
            channel,
            text,
            event="foreign",
            from_user_id="other",
            to_user_id=channel.account_id,
        )
        assert foreign is None
        forwarded = command(
            config,
            channel,
            text,
            event="forwarded",
            forwarded=True,
            to_user_id=channel.account_id,
        )
        confirmations.process(config, channel)
        assert confirmations.receipt(config, channel, forwarded) == {}
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2
    assert not db.execute("SELECT * FROM document_creation_deliveries").fetchall()


def test_moved_analysis_bundle_stays_excluded_and_revoked_source_cannot_disclose(config, completed):
    db, _, attempt, raw, _ = completed
    vault = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    result = save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    root = config.project_root / dict(config.vaults)["personal"]
    (root / result["relative_path"]).rename(root / "Moved-analysis")
    assert not search.search(config, db, attempt.client_id, "100 psi", [vault])["results"]
    (root / "Moved-analysis").rename(root / result["relative_path"])
    auth.set_grant(db, attempt.client_id, vault, query=False, original=False, add=True)
    with pytest.raises(auth.Denied):
        save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)


def test_user_revision_of_analysis_remains_nonindependent_in_next_model_request(config, completed):
    from fiber_bundle import document_creation_contract as contract
    from fiber_bundle import document_creation_request as request
    from fiber_bundle.inputs import canonical

    db, policy, attempt, raw, _ = completed
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    vault = plan.value()["input"]["vault_ids"][0]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    result = save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    root = config.project_root / dict(config.vaults)["personal"] / result["relative_path"]
    (root / "note.md").write_text("Human revision 100 psi")
    response = search.search(config, db, attempt.client_id, "100 psi", [vault])
    item = response["results"][0]
    assert item["source_kind"] == "user_revision" and item["independent_evidence"] is False
    source = plan.value()["input"]
    source["sources"] = [item]
    source["quotations"] = [{"citation": item["citation"], "quote": item["snippet"]}]
    next_plan = replace(plan, input_json=canonical(source))
    contract.validate(next_plan)
    wire = request.request(policy.policy(), next_plan)
    content = json.loads(wire["messages"][1]["content"])
    assert content["analysis_citations"] == [item["citation"]]
    assert content["analysis_is_independent_evidence"] is False


def test_continuation_sidecar_fault_recovers_only_same_frozen_child(config, completed, monkeypatch):
    from fiber_bundle import document_creation_ledger as ledger

    db, _, attempt, _, _ = completed
    original = ledger.append

    def fault(*args, **kwargs):
        raise OSError("sidecar fault")

    monkeypatch.setattr(ledger, "append", fault)
    with pytest.raises(OSError, match="sidecar fault"):
        enqueue(config, db, attempt)
    child = db.execute("SELECT child_task_id FROM document_creation_continuations").fetchone()[0]
    monkeypatch.setattr(ledger, "append", original)
    result = enqueue(config, db, attempt)
    assert result["child"]["task_id"] == child and result["state"] == "ready"
    assert db.execute("SELECT count(*) FROM document_creation_continuations").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 2


def test_archived_draft_and_provenance_enforce_frozen_audience_even_before_version_link(
    config, completed
):
    from fiber_bundle import operations, publication_originals

    db, _, attempt, raw, _ = completed
    vault = tasks.plan(config, db, attempt.client_id, attempt.task_id).value()["input"][
        "vault_ids"
    ][0]
    save.prepare(
        config,
        db,
        attempt.client_id,
        attempt.task_id,
        vault,
        expected_artifact_sha256=digest(raw),
        allow_save=True,
    )
    result = save.publish(config, db, attempt.client_id, attempt.task_id, allow_save=True)
    version = result["source_version_id"]
    asset = db.execute(
        "SELECT asset_id FROM version_assets WHERE version_id=? ORDER BY ordinal", (version,)
    ).fetchone()[0]
    newcomer = auth.create_client(db, "late-original-reader", False)["id"]
    auth.set_grant(db, newcomer, vault, query=True, original=True, add=False)
    assert not any(
        v["id"] == version for v in operations.versions(config, db, newcomer, vault, 0, 100)
    )
    assert publication_originals.visible(db, newcomer, version) == []
    for state in ("published", "publishing"):
        db.execute("UPDATE document_creation_saves SET source_version_id=NULL,state=?", (state,))
        with pytest.raises(auth.Denied):
            operations.original(config, db, newcomer, vault, version, asset, 8 * 1024 * 1024)
    auth.set_grant(db, attempt.client_id, vault, query=True, original=True, add=True)
    assert (
        operations.original(config, db, attempt.client_id, vault, version, asset, 8 * 1024 * 1024)
        == raw
    )
