"""Safe failure evidence and recovery history for the original intake batch."""

import time
from typing import Any

from ..config import InvalidInput
from ..processing_errors import MESSAGES
from . import diagnostics


def capture(error: Exception, phase: str, originals_saved: bool) -> dict[str, Any]:
    code = (
        str(error)
        if isinstance(error, InvalidInput) and str(error) in MESSAGES
        else "intake_processing_interrupted"
        if originals_saved
        else "intake_archive_interrupted"
    )
    return {
        "code": code,
        "phase": phase,
        "originals_saved": originals_saved,
        "observed_at": time.time(),
        **diagnostics.failure(error),
    }


def restore(value: dict[str, Any], old: dict[str, Any], reason: str | None) -> None:
    """Keep completed stages and delivery ledgers; only explicit recovery clears failure."""
    for key in (
        "receipts",
        "original_projections",
        "automatic_imports",
        "channel_batch",
        "receipt_state",
        "receipt_event",
        "receipt_updates",
        "receipt_tracking",
    ):
        if key in old:
            value[key] = old[key]
    # Reuse any completed or ambiguous link work rather than re-extracting its URL.
    if old.get("links"):
        if [x["original_url"] for x in old["links"]] != [x["original_url"] for x in value["links"]]:
            raise InvalidInput("intake_batch_changed")
        value["links"] = old["links"]
    history = list(old.get("failure_history", []))
    if old.get("failure"):
        history.append(old["failure"])
    elif reason:
        history.append(
            {"code": reason, "phase": "legacy", "originals_saved": bool(old.get("receipts"))}
        )
    value["failure_history"] = history
    if "receipt_tracking" in value:
        value["receipt_tracking"] = {**value["receipt_tracking"], "done": False, "next_check": 0}
