"""Permission withdrawal, bounded queues and command boundaries with real isolated storage."""

import json

import pytest
from test_channel_intake import count, enable, receive, records
from test_channel_intake import entry as entry
from test_feishu_ingress import freeze, payload
from test_weixin_inbox import binding as binding

from fiber_bundle import auth, database, feishu_receiver
from fiber_bundle.channels import intake_actions, intake_contract, intake_processing
from fiber_bundle.channels import intake_repository as repository
from fiber_bundle.config import InvalidInput
from fiber_bundle.weixin_store_dispatch import worker_lock


def test_platform_backlog_captured_after_enable_is_still_manual(config, entry):
    policy = enable(config, entry)
    event = payload(body={"text": "生效之前发送、连接之后才收到的合成资料"})
    event["event"]["message"]["create_time"] = str(int((policy["updated_at"] - 60) * 1000))
    feishu_receiver.capture(config, entry[1], event)
    freeze(config, entry[1])
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    assert records(config, entry)[0]["reason"] == "intake_contains_historical_messages"
    assert count(config, "receipts") == 0


def test_command_with_disallowed_link_never_becomes_manual_material(config, entry):
    policy = enable(config, entry, materials=["image"])
    batch = receive(config, entry, text="查询：压力 https://unapproved.org/a")
    intake_processing.advance(config, entry[0]["id"])
    assert records(config, entry)[0]["reason"] == "intake_command_requires_routing"
    with pytest.raises(InvalidInput, match="command_requires_routing"):
        intake_actions.select(
            config,
            entry[0]["id"],
            intake_contract.BatchAction(expected_revision=policy["revision"], batch_ids=[batch]),
        )
    assert count(config, "receipts") == 0


@pytest.mark.parametrize("grouping,expected", [("batch", 1), ("message", 2)])
def test_explicit_grouping_preserves_each_message(config, entry, grouping, expected):
    enable(config, entry, grouping=grouping)
    for index in range(2):
        feishu_receiver.capture(
            config,
            entry[1],
            payload(body={"text": f"合成顺序消息 {index}"}, identity=f"group-{index}"),
        )
    batch = freeze(config, entry[1])
    intake_processing.advance(config, entry[0]["id"])
    row = records(config, entry)[0]
    assert row["batch_id"] == batch
    assert len(json.loads(row["value"])["receipts"]) == expected
    assert count(config, "receipts") == expected
    with database.connect(config.state_root) as main:
        assert (
            main.execute("SELECT count(*) FROM version_assets WHERE role='body'").fetchone()[0] == 2
        )


def test_pause_resume_keeps_pending_messages_and_consumption(config, entry):
    enable(config, entry)
    receive(config, entry)
    path = config.state_root / entry[0]["settings_path"]
    original = json.loads(path.read_text())
    path.write_text(json.dumps({**original, "enabled": False}))
    assert intake_processing.advance(config, entry[0]["id"]) == 0
    assert "intake_channel_paused" in intake_actions.inspect(config, entry[0]["id"])["blockers"]
    assert count(config, "receipts") == 0
    path.write_text(json.dumps(original))
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert count(config, "receipts") == 1


def test_target_becoming_read_only_blocks_commit_and_reports_reason(config, entry):
    enable(config, entry)
    receive(config, entry)
    with database.connect(config.state_root) as main:
        main.execute("UPDATE vaults SET read_only=1 WHERE id=?", (entry[1].vault_id,))
    with pytest.raises(auth.Denied):
        intake_processing.advance(config, entry[0]["id"])
    assert "intake_read_only_vault" in intake_actions.inspect(config, entry[0]["id"])["blockers"]
    assert count(config, "receipts") == 0


def test_100_missing_batches_do_not_starve_complete_new_message(config, entry):
    enable(config, entry)
    for index in range(100):
        feishu_receiver.capture(
            config,
            entry[1],
            payload(
                "image", {"image_key": f"synthetic-image-{index}"}, identity=f"missing-{index}"
            ),
        )
        freeze(config, entry[1])
    assert intake_processing.prepare(config, entry[0]["id"]) == 0
    assert len(records(config, entry)) == 100
    batch = receive(config, entry, identity="later-complete")
    assert intake_processing.advance(config, entry[0]["id"]) == 1
    assert records(config, entry)[-1]["batch_id"] == batch
    assert records(config, entry)[-1]["state"] == "stored"


def test_manual_recovery_cannot_race_active_archival_worker(config, entry):
    policy = enable(config, entry)
    batch = receive(config, entry)
    with worker_lock(config), pytest.raises(InvalidInput, match="worker already active"):
        intake_actions.select(
            config,
            entry[0]["id"],
            intake_contract.BatchAction(expected_revision=policy["revision"], batch_ids=[batch]),
        )
    assert count(config, "receipts") == 0
    assert not records(config, entry)


def test_zero_attachment_budget_is_visible_without_attempting_download(config, entry):
    enable(config, entry)
    feishu_receiver.capture(config, entry[1], payload("image", {"image_key": "synthetic-image"}))
    freeze(config, entry[1])
    intake_processing.advance(config, entry[0]["id"])
    state = intake_actions.inspect(config, entry[0]["id"])
    assert state["records"][0]["reason"] == "intake_download_budget_exhausted"
    assert state["used"]["download"] == 0


def test_review_acknowledgements_are_real_booleans():
    with pytest.raises(ValueError):
        intake_contract.Review(
            digest="a" * 64,
            reason="合成审核",
            source_reviewed=1,
            text_reviewed=True,
            images_reviewed=True,
        )


def test_overview_uses_new_switch_and_cumulative_budget_without_enabling_queries(config, entry):
    from fiber_bundle import feishu_runtime
    from fiber_bundle.channels import health, legacy

    enable(config, entry, download_limit=2)
    with (
        database.connect(config.state_root) as main,
        feishu_runtime.Instance(legacy.home(config, entry[0])) as instance,
    ):
        instance.status("connected", {})
        import time

        # The receiver lease alone is insufficient evidence of an active intake worker.
        (legacy.home(config, entry[0]) / "intake-status.json").write_text(
            json.dumps({"state": "ready", "observed_at": time.time()})
        )
        current, _ = repository.channel(config, entry[0]["id"])
        view = health.present(config, main, current)
        assert view["capabilities"]["store"]["available"]
        assert view["capabilities"]["download"]["available"]
        assert not view["capabilities"]["query"]["available"]
        assert view["intake"]["target_name"] == "personal"
        enable(config, entry, enabled=False)
        view = health.present(config, main, current)
        assert not view["capabilities"]["store"]["enabled"]
        assert not view["capabilities"]["download"]["available"]
