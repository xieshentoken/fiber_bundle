"""Automatic intake uses real isolated journals, grants and Store; no remote services."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest
from test_feishu_ingress import freeze, payload
from test_weixin_inbox import binding as binding

from fiber_bundle import auth, database, feishu_receiver
from fiber_bundle.channels import intake_contract as contract
from fiber_bundle.channels import intake_links as links
from fiber_bundle.channels import intake_processing as processing
from fiber_bundle.channels import intake_repository as repository
from fiber_bundle.channels import legacy
from fiber_bundle.channels import store as channels
from fiber_bundle.config import InvalidInput


@pytest.fixture
def entry(config, binding):
    bound = replace(binding, account_id="feishu:cli_synthetic", owner_id="ou_owner")
    feishu_receiver.initialize(config, bound)
    home = config.state_root / "configs/hermes-feishu-synthetic"
    home.mkdir(parents=True)
    path = home / "receiver.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "enabled": True,
                "app_id": "cli_synthetic",
                "owner_id": bound.owner_id,
                "home": str(home.relative_to(config.state_root)),
                "client_id": bound.client_id,
                "vault_id": bound.vault_id,
            }
        )
    )
    return legacy.entry(config, path), bound


def enable(config, entry, **extra):
    channel, bound = entry
    _, prior = repository.channel(config, channel["id"])
    return repository.save(
        config,
        channel["id"],
        contract.Save(
            expected_revision=prior["revision"],
            settings=contract.Settings(
                **{
                    **prior["settings"],
                    "enabled": True,
                    "target_vault_id": bound.vault_id,
                    "store_limit": 10,
                    **extra,
                }
            ),
        ),
    )


def records(config, entry):
    with channels.readonly(config) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? ORDER BY created",
                (entry[0]["id"],),
            )
        ]


def receive(config, entry, text="合成青松 2.4 MPa", identity="one"):
    feishu_receiver.capture(config, entry[1], payload(body={"text": text}, identity=identity))
    return freeze(config, entry[1])


def count(config, table):
    with database.connect(config.state_root) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_default_off_new_watermark_and_idempotent_archive(config, entry):
    assert not repository.channel(config, entry[0]["id"])[1]["settings"]["enabled"]
    receive(config, entry, identity="old")
    assert processing.advance(config, entry[0]["id"]) == 0
    enable(config, entry)
    assert processing.advance(config, entry[0]["id"]) == 0
    batch = receive(config, entry, identity="new")
    assert processing.advance(config, entry[0]["id"]) == 1
    assert processing.advance(config, entry[0]["id"]) == 0
    feishu_receiver.initialize(config, entry[1])
    assert processing.advance(config, entry[0]["id"]) == 0
    row = records(config, entry)[0]
    assert row["batch_id"] == batch and row["state"] == "stored"
    assert count(config, "receipts") == 1
    assert count(config, "model_calls") == count(config, "execution_runs") == 0
    assert repository.channel(config, entry[0]["id"])[1]["after_row"] == 1


def test_closed_policy_never_replays_and_consumption_does_not_reset(config, entry):
    enable(config, entry, store_limit=1)
    receive(config, entry)
    processing.advance(config, entry[0]["id"])
    enable(config, entry, enabled=False, store_limit=1)
    receive(config, entry, identity="closed")
    enable(config, entry, store_limit=1)
    assert processing.advance(config, entry[0]["id"]) == 0
    receive(config, entry, identity="exhausted")
    processing.advance(config, entry[0]["id"])
    assert records(config, entry)[-1]["reason"] == "intake_store_budget_exhausted"
    assert count(config, "receipts") == 1
    with channels.readonly(config) as db:
        assert repository.consumed(db, entry[0]["id"])["store"] == 1


def test_mixed_disallowed_link_holds_whole_batch_and_query_never_archives(config, entry):
    enable(config, entry)
    receive(config, entry, text="来源 https://mp.weixin.qq.com/s/one https://outside.org/a")
    processing.advance(config, entry[0]["id"])
    assert records(config, entry)[0]["reason"] == "intake_site_not_allowed"
    receive(config, entry, text="查询：青松", identity="query")
    processing.advance(config, entry[0]["id"])
    assert records(config, entry)[-1]["reason"] == "intake_command_requires_routing"
    assert count(config, "receipts") == 0


def test_revocation_and_target_writable_check(config, entry):
    enable(config, entry)
    receive(config, entry)
    main = database.connect(config.state_root)
    auth.set_grant(
        main, entry[1].client_id, entry[1].vault_id, query=False, original=False, add=False
    )
    main.close()
    with pytest.raises(auth.Denied):
        processing.advance(config, entry[0]["id"])
    assert count(config, "receipts") == 0


def test_policy_cas_and_schema_migration_preserve_rows(config, entry):
    enable(config, entry)
    with pytest.raises(InvalidInput, match="revision_conflict"):
        repository.save(
            config, entry[0]["id"], contract.Save(expected_revision=0, settings=contract.Settings())
        )
    path = config.state_root / "message-channels.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
    before = path.read_bytes()
    repository.channel(config, entry[0]["id"])
    assert path.read_bytes() == before
    with channels.opened(config) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert repository.policy(db, entry[0]["id"])["settings"]["enabled"]


@pytest.mark.parametrize(
    "value",
    [
        "https://x.com.evil.org/a",
        "https://x.com@evil.org/a",
        "https://127.0.0.1/a",
        "http://x.com/a",
    ],
)
def test_website_rules_are_hosts_not_substrings(value):
    assert not links.allowed(value, contract.Settings().model_dump())


def test_empty_allowlist_custom_domains_and_redirects():
    settings = contract.Settings(sites=[], custom_domains=["papers.org"]).model_dump()
    assert not links.allowed("https://mp.weixin.qq.com/s/a", settings)
    assert links.allowed("https://www.papers.org/paper", settings)
    assert not links.allowed("https://papers.org.evil.org/a", settings)
    assert not links.allowed("https://private.papers.org/a", settings)
    with pytest.raises(InvalidInput):
        links.checked_redirects(["https://www.papers.org/a", "https://evil.org/a"], settings)


def test_history_is_only_queued_by_explicit_action_even_when_switch_off(config, entry, http):
    from fiber_bundle.channels import intake_actions

    batch = receive(config, entry, identity="historical")
    saved = enable(config, entry, enabled=False)
    prefix = f"/admin/channels/{entry[0]['id']}/intake"
    status = http.get(prefix)
    assert status.status_code == 200, status.text
    assert status.json()["history"][0]["batch_id"] == batch
    assert status.json()["targets"][0]["id"] == entry[1].vault_id
    response = http.post(
        prefix + "/batches", json={"expected_revision": saved["revision"], "batch_ids": [batch]}
    )
    assert response.status_code == 202, response.text
    assert count(config, "receipts") == 0
    assert processing.advance(config, entry[0]["id"]) == 1
    assert count(config, "receipts") == 1
    assert intake_actions.inspect(config, entry[0]["id"])["records"][0]["value"]["manual"]


def test_interrupted_archive_requires_explicit_recovery_and_does_not_duplicate(
    config, entry, monkeypatch
):
    from fiber_bundle import weixin_store
    from fiber_bundle.channels import intake_actions

    saved = enable(config, entry)
    batch = receive(config, entry)
    transfer = weixin_store.transfer

    def interrupted(*args, **kwargs):
        transfer(*args, **kwargs)
        raise InvalidInput("synthetic interruption after Store commit")

    monkeypatch.setattr(weixin_store, "transfer", interrupted)
    with pytest.raises(InvalidInput):
        processing.advance(config, entry[0]["id"])
    assert count(config, "receipts") == 1
    assert processing.advance(config, entry[0]["id"]) == 0
    monkeypatch.setattr(weixin_store, "transfer", transfer)
    intake_actions.select(
        config,
        entry[0]["id"],
        contract.BatchAction(expected_revision=saved["revision"], batch_ids=[batch]),
    )
    assert processing.advance(config, entry[0]["id"]) == 1
    assert count(config, "receipts") == 1
    assert records(config, entry)[0]["state"] == "stored"
    with channels.readonly(config) as db:
        assert repository.consumed(db, entry[0]["id"])["store"] == 1


def test_selected_target_is_explicit_and_never_falls_back(config, entry):
    main = database.connect(config.state_root)
    supplier = main.execute("SELECT id FROM vaults WHERE name='supplier'").fetchone()[0]
    with pytest.raises(auth.Denied):
        enable(config, entry, target_vault_id=supplier)
    auth.set_grant(main, entry[1].client_id, supplier, query=False, original=False, add=True)
    main.close()
    enable(config, entry, target_vault_id=supplier)
    receive(config, entry)
    processing.advance(config, entry[0]["id"])
    with database.connect(config.state_root) as main:
        assert main.execute("SELECT vault_id FROM sources").fetchone()[0] == supplier


def test_picture_file_missing_budget_and_downloaded_bytes(config, entry):
    import io
    from types import SimpleNamespace

    enable(config, entry, download_limit=1)
    feishu_receiver.capture(config, entry[1], payload("image", {"image_key": "synthetic-image"}))
    freeze(config, entry[1])
    processing.prepare(config, entry[0]["id"])
    assert records(config, entry)[0]["state"] == "waiting"
    stream = io.BytesIO(b"\xff\xd8\xffsynthetic-image-original")
    calls = []

    def get(request):
        calls.append(request)
        return SimpleNamespace(success=lambda: True, file=stream)

    async def blocking(function, request):
        return function(request)

    adapter = SimpleNamespace(
        _build_message_resource_request=lambda **values: values,
        _run_blocking=blocking,
        _client=SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message_resource=SimpleNamespace(get=get)))
        ),
    )
    assert asyncio.run(processing.download(config, entry[0]["id"], adapter))
    assert stream.closed
    assert processing.advance(config, entry[0]["id"]) == 1
    assert not asyncio.run(processing.download(config, entry[0]["id"], adapter))
    assert len(calls) == 1
    with channels.readonly(config) as db:
        assert repository.consumed(db, entry[0]["id"])["download"] == 1


def test_no_url_wechat_channels_card_is_archived_as_pending_content(config, entry):
    enable(config, entry)
    feishu_receiver.capture(
        config, entry[1], payload("interactive", {"title": "微信视频号", "content": "平台分享"})
    )
    freeze(config, entry[1])
    processing.advance(config, entry[0]["id"])
    row = records(config, entry)[0]
    assert row["state"] == "stored"
    value = json.loads(row["value"])
    assert value["cards"] == [{"kind": "wechat_channels", "content_state": "card_only"}]
    assert count(config, "receipts") == 1
