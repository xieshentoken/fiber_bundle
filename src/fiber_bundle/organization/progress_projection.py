"""Read-only progress contract derived from persisted task facts, never elapsed time."""

from typing import Any


def project_progress(job: dict[str, Any]) -> dict[str, Any]:
    items = job.get("items") or []
    total = len(items)

    def resolved(item: dict[str, Any]) -> bool:
        return (item.get("manual_resolution") or {}).get("state") == "resolved"

    done = sum(item.get("state") == "done" or resolved(item) for item in items)
    completed = job.get("completed")
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        completed = done
    counts = {
        "analyzed": sum(bool(item.get("evaluated")) for item in items),
        "processed": completed,
        "mounted": sum(
            (item.get("membership_observation") or {}).get("state") == "current" for item in items
        ),
        "archived": sum(item.get("phase") == "archived" for item in items),
        "waiting": sum(item.get("state") in {"waiting_file", "waiting_archive"} for item in items),
        "review": sum(item.get("status") == "review" and not resolved(item) for item in items),
        "archive_pending": sum(
            bool(item.get("archive_operation"))
            and item.get("phase") != "archived"
            and not item.get("archive_wait")
            and not item.get("archive_deferral")
            for item in items
        ),
    }
    detail = job.get("error_detail") or {}
    if job.get("state") == "closed":
        phase = "closed"
    elif job.get("stage") == "rollback":
        phase = "rollback"
    elif job.get("stage") == "planning" or job.get("state") == "preview":
        phase = "preview" if job.get("state") == "preview" else "analysis"
    elif detail.get("phase") in {"archive_committed_verify", "archive_finalize_inventory"}:
        phase = "finalizing"
    elif job.get("blocking_task") or job.get("state") == "waiting_task":
        phase = "waiting_task"
    elif job.get("state") == "waiting_file":
        phase = (
            "waiting_link" if any(item.get("archive_wait") for item in items) else "waiting_file"
        )
    elif job.get("state") == "completed":
        phase = "applied"
    else:
        phase = "applying"
    value = counts["analyzed"] if phase in {"analysis", "preview"} else completed
    maximum = total
    if phase == "rollback":
        value, maximum = job.get("rollback_progress", 0), job.get("rollback_total", 0)
    return {
        **job,
        "execution_progress": {
            "schema": "organization-progress/v1",
            "phase": phase,
            "completed": value,
            "total": maximum,
            "counts": counts,
        },
    }
