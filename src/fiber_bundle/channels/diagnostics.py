"""Local exception categories and code locations, without exception text or locals."""

import sqlite3
from pathlib import Path
from typing import Any

from ..config import InvalidInput

KNOWN = frozenset(
    {
        "channel_credentials_rejected",
        "channel_connection_failed",
        "channel_connection_stopped",
        "channel_weixin_login_not_completed",
        "channel_weixin_poll_rejected",
        "channel_weixin_poll_exhausted",
        "channel_weixin_response_invalid",
        "channel_delivery_requires_reconciliation",
        "channel_capture_failed",
    }
)


def failure(error: Exception) -> dict[str, Any]:
    if isinstance(error, InvalidInput) and str(error) in KNOWN:
        code = str(error)
    elif isinstance(error, sqlite3.Error):
        code = "channel_storage_failed"
    elif isinstance(error, TimeoutError):
        code = "channel_connection_timeout"
    elif isinstance(error, OSError):
        code = "channel_local_io_failed"
    elif isinstance(error, InvalidInput):
        code = "channel_runtime_validation_failed"
    else:
        code = "channel_internal_failed"
    root = Path(__file__).resolve().parents[1]
    locations = []
    trace = error.__traceback__
    while trace:
        path = Path(trace.tb_frame.f_code.co_filename).absolute()
        if path.is_relative_to(root) and path.suffix == ".py":
            locations.append(
                {
                    "module": path.relative_to(root).with_suffix("").as_posix().replace("/", "."),
                    "line": trace.tb_lineno,
                }
            )
        trace = trace.tb_next
    return {"error": code, "diagnostic": {"locations": locations[-8:]}}
