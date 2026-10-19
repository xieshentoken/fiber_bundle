from unittest.mock import patch

import pytest
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_cloud_answer_tasks import result
from test_cloud_answer_worker import report
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import cloud_answer_execution as execution
from fiber_bundle import cloud_answer_gateway as gateway
from fiber_bundle import cloud_answer_tasks as tasks
from fiber_bundle import execution_ledger
from fiber_bundle.config import InvalidInput


@pytest.mark.parametrize("point", ["start", "reserve", "return", "settle", "result", "commit"])
def test_interrupt_at_every_durable_boundary_preserves_attempt_and_known_usage(
    config, route, owner, cloud_task, point
):
    # Inject after a real boundary once; cleanup then runs against the real DB.
    target = {
        "start": "start",
        "reserve": "reserve",
        "return": "claim",
        "settle": "settle",
        "result": "finish",
        "commit": "finish",
    }[point]
    module = tasks if target in ("claim", "finish") else execution_ledger
    original = getattr(module, target)
    fired = False

    def once(*args, **kwargs):
        nonlocal fired
        if target == "finish" and point == "result" and not fired:
            fired = True
            raise KeyboardInterrupt()
        value = original(*args, **kwargs)
        if not fired:
            fired = True
            raise KeyboardInterrupt()
        return value

    with (
        patch.object(module, target, side_effect=once),
        patch.object(gateway, "complete", return_value=result()) as provider,
    ):
        with pytest.raises(KeyboardInterrupt):
            execution.execute(config, route, owner[0], cloud_task, allow_inference=True)
    status = report(config, owner, cloud_task)
    assert status["state"] == ("succeeded" if point == "commit" else "interrupted")
    if point == "start":
        assert status["cloud_call"] is None
    else:
        assert status["cloud_call"]["state"] == (
            "settled" if point in ("settle", "result", "commit") else "uncertain"
        )
    assert provider.call_count == (1 if point in ("settle", "result", "commit") else 0)
    with pytest.raises(InvalidInput):
        execution.execute(config, route, owner[0], cloud_task, allow_inference=True)
