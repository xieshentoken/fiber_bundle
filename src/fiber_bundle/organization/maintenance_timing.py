"""Small per-phase counters; no source text, model payloads or additional writes."""

import time
from contextlib import contextmanager
from typing import Any


@contextmanager
def measure(job: Any, phase: str) -> Any:
    start = time.monotonic()
    job["activity"] = {"phase": phase, "started_at": time.time()}
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        totals = job.setdefault("timings", {}).setdefault(phase, {"seconds": 0.0, "units": 0})
        totals["seconds"] = round(totals["seconds"] + elapsed, 6)
        totals["units"] += 1
        job["activity"]["updated_at"] = time.time()
