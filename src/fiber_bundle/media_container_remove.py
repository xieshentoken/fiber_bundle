"""Bounded removal reconciliation for an already scope-verified Docker content identity."""

import re
import subprocess
import time

from .config import InvalidInput


def remove(identity: str) -> None:
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{64}", identity):
        raise InvalidInput("media_container_removal_identity")
    result = subprocess.run(
        ["docker", "rm", "--force", identity],
        timeout=15,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return
    # A concurrent cancel/recovery may already own this removal. Never repeat the mutation.
    wait_for_absence("id=" + identity)


def wait_for_absence(filter_value: str) -> None:
    """Read-only reconciliation also covers an inspect racing with another deletion."""
    scope = r"id=[a-f0-9]{64}|name=\^/fiber-(?:processing|link)-[a-f0-9-]{36}\$"
    if not re.fullmatch(scope, filter_value):
        raise InvalidInput("media_container_absence_scope")
    deadline = time.monotonic() + 3
    while True:
        remaining = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", filter_value],
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise InvalidInput("media_container_removal_unresolved")
        time.sleep(0.1)
