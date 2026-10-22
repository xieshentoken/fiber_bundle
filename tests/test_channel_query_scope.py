"""Two synthetic Vaults: independent ingress, explicit union/single queries, no live delivery."""

import asyncio
import json

import pytest
from test_channel_natural_query import envelope
from test_channel_natural_query import model_channel as model_channel
from test_message_channels import bind
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import auth, database, model_gateway, vault_catalog
from fiber_bundle.channels import configuration, dispatch, query_scope, store
from fiber_bundle.config import InvalidInput


def configure(config, entry, tmp_path):
    root = tmp_path / "supplier-notes"
    root.mkdir()
    (root / "a.md").write_text("# 青松供应商\n青松供应商声称可提供压力 3.2 MPa 的产品，尚未核验。")
    with database.connect(config.state_root) as main:
        preview = vault_catalog.preview(
            config, main, name="供应商库", root=str(root), scope={"include": [""], "exclude": []}
        )
        other = vault_catalog.attach(config, main, preview["preview_id"])["vault_id"]
        with store.opened(config) as side:
            prior = store.get(side, entry["id"])
            side.execute("UPDATE channels SET state='paused' WHERE id=?", (entry["id"],))
        settings = {
            **prior["value"],
            "purpose": "query",
            "add": False,
            "natural_questions": True,
            "policy_version": 2,
            "query_vaults": [
                {"vault_id": prior["value"]["vault_id"], "alias": "个人"},
                {"vault_id": other, "alias": "供应商"},
            ],
        }
        saved = configuration.save(
            config,
            main,
            configuration.Save(
                id=entry["id"],
                expected_revision=prior["revision"],
                settings=configuration.Settings.model_validate(settings),
            ),
        )
    bind(config, saved)
    return saved, other, root


@pytest.fixture
def multi(config, channel, tmp_path):
    entry, root = channel
    saved, other, second = configure(config, entry, tmp_path)
    return saved, root, other, second


def run(config, entry, text, event="synthetic-query"):
    assert dispatch.capture(config, entry["id"], entry["revision"], event, "ou_synthetic", text)
    dispatch.advance(config, entry["id"], entry["revision"])
    with store.opened(config) as side:
        row = side.execute(
            "SELECT * FROM events WHERE channel_id=? AND event_id=?", (entry["id"], event)
        ).fetchone()
    assert row["state"] == "ready"
    return json.loads(row["value"])["result"]


@pytest.mark.parametrize("body", ["查询：青松", "查询:青松"])
def test_default_union_and_natural_questions(config, multi, body):
    entry, _, other, _ = multi
    result = run(config, entry, body)
    assert result["searched_vault_ids"] == [entry["settings"]["vault_id"], other]
    assert {s["vault_id"] for s in result["sources"]} == set(result["searched_vault_ids"])
    assert result["query_scope_labels"] == ["个人", "供应商"]
    assert len({s["citation"] for s in result["sources"]}) == 2


def test_single_library_never_reads_other_and_next_message_defaults_all(config, multi, monkeypatch):
    entry, _, other, _ = multi
    from fiber_bundle import vaults

    original = vaults.checked_vault
    with monkeypatch.context() as patch:

        def checked(c, v):
            assert v["id"] == other, "unselected library was accessed"
            return original(c, v)

        patch.setattr(vaults, "checked_vault", checked)
        selected = run(config, entry, "查询[供应商]：青松", "one")
    assert selected["searched_vault_ids"] == [other]
    assert selected["query_scope_labels"] == ["供应商"]
    union = run(config, entry, "查询：青松", "two")
    assert len(union["searched_vault_ids"]) == 2


@pytest.mark.parametrize(
    "body,code",
    [
        ("查询本库：青松", "channel_query_command_removed"),
        ("查询[不存在]：青松", "channel_query_alias_unknown"),
        ("查询[供应商：青松", "channel_query_command_invalid"),
        ("查询 [个人]：青松", "channel_query_command_invalid"),
        ("查询【个人】：青松", "channel_query_command_invalid"),
        ("查询： ", "channel_query_question_required"),
    ],
)
def test_control_diagnostics_without_model_or_material_ingress(
    config, multi, monkeypatch, body, code
):
    entry, _, _, _ = multi
    monkeypatch.setattr(model_gateway, "complete", lambda *a: pytest.fail("control called model"))
    result = run(config, entry, body)
    assert result["status"] == "query_hint" and result["error_code"] == code
    assert result["sources"] == []
    assert "查询" in result["answer"]


def test_revoked_secondary_blocks_union_without_blocking_explicit_healthy_library(config, multi):
    entry, _, other, _ = multi
    with database.connect(config.state_root) as main:
        auth.set_grant(
            main, entry["settings"]["client_id"], other, query=False, original=False, add=False
        )
    assert run(config, entry, "查询：青松", "all")["status"] == "query_hint"
    assert run(config, entry, "查询[个人]：青松", "one")["searched_vault_ids"] == [
        entry["settings"]["vault_id"]
    ]


def test_unlinked_primary_does_not_retarget_alias_or_block_healthy_secondary(config, multi):
    entry, _, other, _ = multi
    with database.connect(config.state_root) as main:
        vault_catalog.unlink(config, main, entry["settings"]["vault_id"])
    assert run(config, entry, "查询[个人]：青松", "missing")["status"] == "query_hint"
    assert run(config, entry, "查询[供应商]：青松", "healthy")["searched_vault_ids"] == [other]


def test_revocation_after_retrieval_prevents_sender(config, multi):
    entry, _, other, _ = multi
    run(config, entry, "查询[供应商]：青松")
    with database.connect(config.state_root) as main:
        auth.set_grant(
            main, entry["settings"]["client_id"], other, query=False, original=False, add=False
        )

    async def forbidden(*args):
        pytest.fail("revoked evidence was sent")

    with pytest.raises(auth.Denied):
        asyncio.run(dispatch.deliver(config, entry["id"], entry["revision"], forbidden))


def test_secondary_grant_is_query_only_and_existing_grants_are_preserved(config, multi):
    entry, _, other, _ = multi
    with database.connect(config.state_root) as main:
        auth.grant(main, entry["settings"]["client_id"], other, "query")
        for action in ["add", "original"]:
            with pytest.raises(auth.Denied):
                auth.grant(main, entry["settings"]["client_id"], other, action)


@pytest.mark.parametrize("alias", ["", "个人", "本库", " a ", "a:b", "A" * 33])
def test_bad_or_duplicate_alias_rejected_without_config_change(config, multi, alias):
    entry, _, _, _ = multi
    with store.readonly(config) as side:
        before = store.get(side, entry["id"])
    settings = {
        **before["value"],
        "query_vaults": [dict(v) for v in before["value"]["query_vaults"]],
    }
    settings["query_vaults"][1]["alias"] = alias
    with database.connect(config.state_root) as main:
        with pytest.raises(InvalidInput, match="scope_invalid"):
            configuration.save(
                config,
                main,
                configuration.Save(
                    id=entry["id"],
                    expected_revision=before["revision"],
                    settings=configuration.Settings.model_validate(settings),
                ),
            )
    with store.readonly(config) as side:
        assert store.get(side, entry["id"]) == before


def test_configuration_change_invalidates_frozen_scope(config, multi):
    entry, _, _, _ = multi
    with store.readonly(config) as side:
        channel = store.get(side, entry["id"])
    frozen = query_scope.snapshot(channel, "个人")
    changed = {**channel, "revision": channel["revision"] + 1}
    with pytest.raises(InvalidInput, match="scope_changed"):
        query_scope.bind(changed, frozen)


@pytest.mark.parametrize("body", ["介绍青松", "青松的压力是多少？", "青松压力资料"])
def test_model_union_uses_existing_two_stages_and_original_cache_once(
    config, model_channel, tmp_path, monkeypatch, body
):
    entry, _ = model_channel
    entry, other, _ = configure(config, entry, tmp_path)
    calls = []

    def complete(policy, identity, payload):
        calls.append(identity)
        if len(calls) == 1:
            return envelope({"queries": ["青松"]})
        packet = json.loads(payload["messages"][1]["content"])
        assert len(packet["quotations"]) == 2
        return envelope(
            {
                "claims": [{"text": "两库分别记录了压力资料。", "citations": ["[1]", "[2]"]}],
                "quotations": packet["quotations"],
            }
        )

    monkeypatch.setattr(model_gateway, "complete", complete)
    result = run(config, entry, body)
    assert set(result["searched_vault_ids"]) == {entry["settings"]["vault_id"], other}
    assert len(calls) == 2
    dispatch.advance(config, entry["id"], entry["revision"])
    assert len(calls) == 2


def test_query_configuration_preserves_existing_saving_and_original_grants(config, multi):
    entry, _, other, _ = multi
    with database.connect(config.state_root) as main:
        main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (other,))
        auth.set_grant(
            main, entry["settings"]["client_id"], other, query=True, original=True, add=True
        )
        with store.opened(config) as side:
            side.execute("UPDATE channels SET state='paused' WHERE id=?", (entry["id"],))
            prior = store.get(side, entry["id"])
        settings = configuration.Settings.model_validate(prior["value"])
        configuration.save(
            config,
            main,
            configuration.Save(
                id=entry["id"], expected_revision=prior["revision"], settings=settings
            ),
        )
        for action in ["query", "original", "add"]:
            auth.grant(main, entry["settings"]["client_id"], other, action)


def test_new_primary_original_flag_does_not_grant_originals_on_shared_query_scope(config, multi):
    entry, _, other, _ = multi
    with database.connect(config.state_root) as main:
        with store.opened(config) as side:
            side.execute("UPDATE channels SET state='paused' WHERE id=?", (entry["id"],))
            prior = store.get(side, entry["id"])
        value = {
            **prior["value"],
            "original": True,
            "query_vaults": list(reversed(prior["value"]["query_vaults"])),
        }
        configuration.save(
            config,
            main,
            configuration.Save(
                id=entry["id"],
                expected_revision=prior["revision"],
                settings=configuration.Settings.model_validate(value),
            ),
        )
        with pytest.raises(auth.Denied):
            auth.grant(main, entry["settings"]["client_id"], other, "original")


def test_two_saving_entries_keep_separate_targets_and_shared_query_cannot_enable_intake(
    config, multi
):
    entry, _, other, _ = multi
    from fiber_bundle.channels import intake_contract, intake_repository
    from fiber_bundle.channels.services import policy

    with database.connect(config.state_root) as main:
        for vid in [entry["settings"]["vault_id"], other]:
            main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (vid,))
            value = {**entry["settings"], "vault_id": vid, "purpose": "intake", "add": True}
            value.pop("query_vaults")
            saving = store.save(config, main, value, None, None)
            with store.opened(config) as side:
                side.execute("UPDATE channels SET owner='ou_synthetic' WHERE id=?", (saving["id"],))
            _, current = intake_repository.channel(config, saving["id"])
            saved = intake_repository.save(
                config,
                saving["id"],
                intake_contract.Save(
                    expected_revision=current["revision"],
                    settings=intake_contract.Settings(
                        policy_version=2, target_vault_id=vid, enabled=True
                    ),
                ),
            )
            assert saved["settings"]["target_vault_id"] == vid
    with pytest.raises(InvalidInput, match="role_unavailable"):
        policy.change(config, entry["id"], "intake", 0, enabled=True)
