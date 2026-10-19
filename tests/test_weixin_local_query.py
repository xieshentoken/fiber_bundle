from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_local_query_tasks import body
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_weixin_inbox import binding as binding
from test_weixin_task_inputs import query_grant, saved

from fiber_bundle import (
    database,
    local_query_ledger,
    local_query_tasks,
    query_policy_store,
    weixin_batches,
    weixin_inbox,
    weixin_query_bridge,
    weixin_task_bindings,
    weixin_task_results,
    weixin_task_status,
)
from fiber_bundle.config import InvalidInput


def test_confirmed_hermes_handoff_local_only_idempotent_and_outbox(config, binding, route, routing):
    query_grant(config, binding)
    bid, _ = saved(config, binding, "consult", ["合成正文一"])
    query_policy_store.save(config, routing, None)
    with ThreadPoolExecutor(2) as pool:
        values = list(
            pool.map(lambda _: weixin_query_bridge.submit(config, binding, bid), range(2))
        )
    assert values[0]["run_id"] == values[1]["run_id"]
    tid = values[0]["run_id"]
    db = database.connect(config.state_root)
    for table in ("execution_runs", "model_calls", "local_query_calls"):
        assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    owned = local_query_ledger.claim(config, db, route, binding.client_id, tid)
    assert (
        local_query_ledger.finish(config, db, route, owned, body(route, owned))["state"]
        == "succeeded"
    )
    db.close()
    assert weixin_task_status.synchronize(config, binding, tid)["run_state"] == "succeeded"
    with weixin_inbox.connection(config, binding) as journal:
        text, fingerprint = weixin_task_results.snapshot_locked(config, journal, binding, bid)
    assert "合成正文一" in text and len(fingerprint) == 64
    assert weixin_query_bridge.submit(config, binding, bid)["state"] == "succeeded"


def test_cancelled_batch_invalidates_local_and_cloud_delivery(config, binding, route):
    query_grant(config, binding)
    bid, _ = saved(config, binding, "consult", ["合成正文"])
    tid = weixin_task_bindings.submit(config, binding, bid, route)["run_id"]
    weixin_batches.choose(config, binding, bid, "cancel")
    assert weixin_task_status.synchronize(config, binding, tid)["run_state"] == "cancelled"
    db = database.connect(config.state_root)
    with pytest.raises(InvalidInput, match="not_replayable"):
        local_query_ledger.claim(config, db, route, binding.client_id, tid)
    db.close()
    with weixin_inbox.connection(config, binding) as journal, pytest.raises(InvalidInput):
        weixin_task_results.snapshot_locked(config, journal, binding, bid)


def test_local_binding_conflicts_old_policy_other_identity_and_changed_route(
    config, binding, route
):
    query_grant(config, binding)
    bid, _ = saved(config, binding, "consult", ["合成正文"])
    first = weixin_task_bindings.submit(config, binding, bid, route)
    with pytest.raises(InvalidInput, match="conflicts"):
        weixin_task_bindings.submit(config, binding, bid, route.cloud_answer)
    with pytest.raises(InvalidInput, match="conflicts"):
        weixin_task_bindings.submit(
            config,
            binding,
            bid,
            replace(route, local_query=replace(route.local_query, model="changed")),
        )
    with pytest.raises(InvalidInput):
        weixin_task_status.synchronize(
            config, replace(binding, owner_id="another-owner"), first["run_id"]
        )


def test_journal_intent_restores_same_local_id_after_transaction_failure(config, binding, route):
    query_grant(config, binding)
    bid, _ = saved(config, binding, "consult", ["合成正文"])
    with (
        patch.object(local_query_tasks, "submit", side_effect=OSError("synthetic")),
        pytest.raises(OSError),
    ):
        weixin_task_bindings.submit(config, binding, bid, route)
    with weixin_inbox.connection(config, binding) as journal:
        tid = journal.execute("SELECT run_id FROM wx_task_bindings").fetchone()[0]
    assert weixin_task_bindings.submit(config, binding, bid, route)["run_id"] == tid
