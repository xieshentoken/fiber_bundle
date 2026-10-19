"""V2 records every operation while model budgets remain the sole business quota."""

import asyncio

import pytest
from test_channel_intake import enable
from test_message_channels import bind

from fiber_bundle import database, feishu_budgets, feishu_runtime, media_subtitles
from fiber_bundle.channels import (
    configuration,
    dispatch,
    health,
    intake_repository,
    store,
    validation,
)
from fiber_bundle.channels.intake_video import duration

pytest_plugins = ["test_channel_intake", "test_message_channels", "test_execution"]


def test_v2_archive_download_receipts_are_unlimited_but_each_identity_is_once(config, entry):
    policy = enable(
        config, entry, policy_version=2, store_limit=0, download_limit=0, outbound_limit=0
    )
    with store.opened(config) as db:
        for kind in ("store", "download", "outbound"):
            for number in range(101):
                target = f"synthetic-{kind}-{number}"
                assert intake_repository.reserve(
                    db, entry[0]["id"], policy["settings"], kind, target
                )
                assert not intake_repository.reserve(
                    db, entry[0]["id"], policy["settings"], kind, target
                )
        current = intake_repository.present(db, entry[0])
        assert current["remaining"] == {"store": None, "download": None, "outbound": None}
        assert current["used"] == {"store": 101, "download": 101, "outbound": 101}


@pytest.mark.parametrize("versioned", [True, False])
def test_v2_zero_legacy_query_and_reply_counts_allow_real_evidence_delivery(
    config, channel, versioned
):
    saved, _ = channel
    with database.connect(config.state_root) as main:
        saved = configuration.save(
            config,
            main,
            configuration.Save(
                id=saved["id"],
                expected_revision=1,
                settings=configuration.Settings(
                    **{
                        **saved["settings"],
                        "policy_version": 2,
                        "query_limit": 0,
                        "model_limit": 0,
                        "outbound_limit": 0,
                    }
                ),
            ),
        )
    current = bind(config, saved)
    if not versioned:
        with store.opened(config) as side:
            side.execute(
                "update channels set value=json_remove(value,'$.policy_version') where id=?",
                (saved["id"],),
            )
            current = store.get(side, saved["id"])
    assert validation.start_issues(current["value"], intake_enabled=False) == []
    sent = []

    async def sender(owner, body, context, guard):
        guard()
        sent.append(body)
        return True

    for number in range(3):
        assert dispatch.capture(
            config, saved["id"], saved["revision"], str(number), current["owner"], "查询本库：压力"
        )
        assert not dispatch.capture(
            config, saved["id"], saved["revision"], str(number), current["owner"], "查询本库：压力"
        )
        dispatch.advance(config, saved["id"], saved["revision"])
        assert (
            asyncio.run(dispatch.deliver(config, saved["id"], saved["revision"], sender)) == "sent"
        )
    assert len(sent) == 3 and all("2.4 MPa" in body for body in sent)
    with database.connect(config.state_root) as main:
        assert main.execute("select count(*) from model_calls").fetchone()[0] == 0
        home = store.home(config, saved["id"])
        with feishu_runtime.Instance(home) as instance:
            instance.status("connected", {})
            feishu_runtime.write(
                home / "channel-status.json",
                {
                    "instance_id": instance.identity,
                    "revision": saved["revision"],
                    "nonce": "synthetic",
                    "state": "query_ready",
                    "platform": "feishu",
                },
            )
            view = health.present(config, main, current)
            assert view["remaining"] == {"query": None, "model": None, "outbound": None}
            assert view["capabilities"]["query"]["available"]


def test_optional_expert_receipt_api_supports_unlimited_without_replay(config, entry):
    from fiber_bundle import weixin_inbox

    feishu_budgets.authorize(config, entry[1], "unlimited-synthetic", 0, 0, 0, 0)
    with weixin_inbox.connection(config, entry[1]) as db:
        assert not feishu_budgets.reserve_locked(
            db, entry[1], "unlimited-synthetic", "outbound", "one"
        )
        assert feishu_budgets.reserve_locked(
            db, entry[1], "unlimited-synthetic", "outbound", "one", unlimited=True
        )
        assert not feishu_budgets.reserve_locked(
            db, entry[1], "unlimited-synthetic", "outbound", "one", unlimited=True
        )


def test_intake_duration_has_no_business_ceiling_and_legacy_interface_is_unchanged():
    import pytest

    from fiber_bundle.config import InvalidInput

    milliseconds = duration(10800, long_video=True)
    assert milliseconds == 10800000
    cues = media_subtitles.parse(
        b"1\n02:59:59,000 --> 03:00:00,000\nend\n", "srt", milliseconds, long_video=True
    )
    assert cues[-1]["end_ms"] == milliseconds
    with pytest.raises(InvalidInput):
        duration(61)
    for bad in [True, float("inf"), 1e308, 0, -1]:
        with pytest.raises(InvalidInput):
            duration(bad, long_video=True)


def test_shared_processing_budget_is_the_only_quota_and_keeps_unknown_reservations(
    config, policy, bound
):
    from dataclasses import replace

    import pytest

    from fiber_bundle import execution_ledger, model_budgets, pi_processing

    selected = replace(
        policy,
        max_input_tokens=100,
        max_output_tokens=100,
        max_calls=1,
        daily_calls=1,
        task_limit_nusd=1,
        daily_limit_nusd=1,
    )
    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=selected.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic", limit=1000),
            ),
        )
        version = execution_ledger.get_run(db, bound[0])["version_id"]
        run = pi_processing.prepare(
            config,
            db,
            selected,
            bound[1],
            bound[2],
            version,
            "article",
            "a" * 64,
            [{"id": "capture:0", "text": "合成"}],
            allow_cloud=True,
        )
        execution_ledger.start(config, db, run, selected)
        for _ in range(12):
            call = execution_ledger.reserve(config, db, run, selected)
            execution_ledger.settle(
                db, call, selected, {"prompt_tokens": 10, "completion_tokens": 10}
            )
        assert (
            db.execute("select count(*) from model_calls where run_id=?", (run,)).fetchone()[0]
            == 12
        )
        unknown = []
        for _ in range(3):
            call = execution_ledger.reserve(config, db, run, selected)
            db.execute("update model_calls set state='uncertain' where id=?", (call,))
            unknown.append(call)
        with pytest.raises(model_budgets.Exhausted):
            execution_ledger.reserve(config, db, run, selected)
        pool = model_budgets.inspect(db)[0]
        assert pool["used"] == 840 and pool["remaining"] == 160
        assert all(
            db.execute("select state from model_calls where id=?", (call,)).fetchone()[0]
            == "uncertain"
            for call in unknown
        )
        # The explicit legacy API still honors its own cost ceiling.
        legacy = execution_ledger.create_run(config, db, selected, bound[1], bound[2], version)
        execution_ledger.start(config, db, legacy, selected)
        with pytest.raises(execution_ledger.BudgetExceeded):
            execution_ledger.reserve(config, db, legacy, selected)
