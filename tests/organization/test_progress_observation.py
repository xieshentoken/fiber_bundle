from copy import deepcopy
from unittest.mock import patch

from fiber_bundle.organization import progress_observation


def test_current_index_does_not_mutate_or_resume_historical_job():
    job = {
        "state": "partial",
        "error": "cloud_item_pending_stale_or_conflicted",
        "index_state": "failed",
        "activity": {"updated_at": 100},
        "completed": 12,
        "coverage": {"complete": False, "unavailable_count": 1},
    }
    before = deepcopy(job)
    value = progress_observation.project(
        job, {"state": "ready", "built_at": 200, "generation": 287, "indexed_generation": 287}
    )
    assert value["state"] == "partial" and value["completed"] == 12
    assert value["index_state"] == "failed"  # Historical outcome remains available.
    assert value["index_observation"]["after_task"]
    assert value["last_progress_at"] == 100
    assert value["coverage"]["historical"]
    assert job == before


def test_old_index_cannot_be_reported_as_recovery_of_later_work():
    value = progress_observation.project(
        {"updated_at": 300, "index_state": "failed"}, {"state": "ready", "built_at": 200}
    )
    assert not value["index_observation"]["after_task"]


def test_observation_reuses_local_status_and_bounds_public_fields(config):
    status = {
        "state": "ready",
        "built_at": 200,
        "notes": 7,
        "sync": {
            "generation": 2,
            "indexed": 2,
            "checked_at": 190,
            "observer_available": True,
            "root": "private path",
            "binding": "private",
        },
    }
    with patch.object(progress_observation, "index_status", return_value=status) as read:
        value = progress_observation.observe(config, "synthetic-vault")
    read.assert_called_once_with(config, "synthetic-vault")
    assert value["state"] == "ready"
    assert "root" not in value and "binding" not in value and "sync" not in value
