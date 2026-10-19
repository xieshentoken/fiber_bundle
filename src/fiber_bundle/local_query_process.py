"""Bounded credential-worker lifetime; caller checks task authority between waits."""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable


class LocalCallStopped(Exception):
    """The owning dispatcher requested a stop; provider computation may continue."""


def run(
    wire: bytes,
    timeout: float,
    should_stop: Callable[[], bool] | None,
    *,
    module: str = "fiber_bundle.local_query_gateway",
) -> subprocess.CompletedProcess[bytes]:
    if module not in {"fiber_bundle.local_query_gateway", "fiber_bundle.cloud_answer_gateway"}:
        raise ValueError("unsupported credential worker")
    command = [sys.executable, "-m", module]
    env = {
        **{
            name: os.environ[name]
            for name in ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "LC_CTYPE")
            if name in os.environ
        },
        "PYTHONNOUSERSITE": "1",
    }
    if should_stop is None:
        return subprocess.run(
            command, input=wire, capture_output=True, timeout=timeout, check=False, env=env
        )
    if should_stop():
        raise LocalCallStopped()
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    ) as process:
        deadline = time.monotonic() + timeout
        incoming: bytes | None = wire
        try:
            while True:
                if should_stop():
                    raise LocalCallStopped()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    out, err = process.communicate(incoming, timeout=min(0.2, remaining))
                    return subprocess.CompletedProcess(command, process.returncode, out, err)
                except subprocess.TimeoutExpired:
                    incoming = None
        finally:
            if process.poll() is None:
                # Isolate credential conversion descendants in this owned group.
                # The unreaped live leader pins its PID until the following wait.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
