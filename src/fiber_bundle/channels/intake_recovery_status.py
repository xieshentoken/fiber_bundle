"""Read-only model recovery facts for channel status and explicit retry confirmation."""

from typing import Any


def project(db: Any, link: dict[str, Any]) -> dict[str, Any]:
    video = link.get("video_processing", {})
    ids = list(link.get("pi_processing", {}).get("runs", {}).values())
    ids += list(video.get("vision_runs", {}).values()) + list(video.get("note_runs", {}).values())
    rows = []
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        rows.extend(
            db.execute(
                "SELECT r.id,r.state,EXISTS(SELECT 1 FROM model_calls AS c "
                "WHERE c.run_id=r.id) AS called FROM execution_runs AS r WHERE id IN ("
                + ",".join("?" for _ in part)
                + ")",
                part,
            ).fetchall()
        )
    states = {row["state"] for row in rows}
    state = next(
        (
            s
            for s in (
                "waiting_budget",
                "running",
                "failed",
                "interrupted",
                "cancelled",
                "queued",
                "succeeded",
            )
            if s in states
        ),
        None,
    )
    from ..video_asr_api import has_result

    asr_retry = False
    asr_states = set()
    for rid in video.get("asr_runs", {}).values():
        row = db.execute(
            "SELECT state FROM model_configuration_calls WHERE id=?", (rid,)
        ).fetchone()
        if row:
            asr_states.add(row["state"])
        asr_retry |= bool(
            row
            and (
                row["state"] in {"failed", "interrupted"}
                or (row["state"] == "running" and link.get("state") != "extracting")
            )
            and not has_result(db, video, rid)
        )
    return {
        "model_state": state
        or next(
            (
                s
                for s in ("running", "failed", "interrupted", "queued", "succeeded")
                if s in asr_states
            ),
            None,
        ),
        "requires_new_model_attempt": asr_retry
        or any(
            row["called"]
            and (
                row["state"] in {"failed", "interrupted", "cancelled", "waiting_budget"}
                or row["id"] in video.get("rejected_note_runs", [])
                or row["id"] in video.get("rejected_vision_runs", [])
                or row["id"] in link.get("pi_processing", {}).get("rejected_runs", [])
            )
            for row in rows
        ),
    }
