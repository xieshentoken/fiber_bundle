from copy import deepcopy

from fiber_bundle.organization.progress_projection import project_progress


def test_closed_progress_retains_persisted_14_of_15_and_separate_outcomes():
    job = {
        "state": "closed",
        "confirmed": True,
        "completed": 14,
        "items": [
            {
                "evaluated": True,
                "state": "done",
                "phase": "archived",
                "membership_observation": {"state": "current"},
            }
            for _ in range(9)
        ]
        + [
            {
                "evaluated": True,
                "state": "waiting_archive",
                "archive_wait": {"state": "blocked"},
                "membership_observation": {"state": "current"},
            }
        ]
        + [{"evaluated": True, "state": "done", "status": "review"} for _ in range(5)],
    }
    before = deepcopy(job)
    packet = project_progress(job)["execution_progress"]
    assert (packet["phase"], packet["completed"], packet["total"]) == ("closed", 14, 15)
    assert packet["counts"] == {
        "analyzed": 15,
        "processed": 14,
        "mounted": 10,
        "archived": 9,
        "waiting": 1,
        "review": 5,
        "archive_pending": 0,
    }
    assert job == before


def test_preview_completion_is_analysis_only_and_finalization_never_counts_an_archive():
    job = {
        "state": "preview",
        "stage": "planning",
        "completed": 0,
        "items": [{"evaluated": True} for _ in range(15)],
    }
    packet = project_progress(job)["execution_progress"]
    assert (packet["phase"], packet["completed"], packet["total"]) == ("preview", 15, 15)
    assert packet["counts"]["processed"] == packet["counts"]["archived"] == 0
    job.update(
        state="partial",
        confirmed=True,
        stage="applying",
        error_detail={"phase": "archive_finalize_inventory", "reason": "directory_changed"},
    )
    job["items"][0].update(archive_operation="original", phase="members_done")
    packet = project_progress(job)["execution_progress"]
    assert packet["phase"] == "finalizing"
    assert packet["completed"] == packet["counts"]["archived"] == 0
    assert packet["counts"]["archive_pending"] == 1
