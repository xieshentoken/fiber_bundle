"""Finite, project-local stop/cleanup worker; restart requires an explicit resume action."""

import argparse
import fcntl
import os
import time
from pathlib import Path

from .. import database
from ..config import Config, load_config
from . import lifecycle_execution, operations, store


def run(config: Config, operation_id: str, *, timeout: float = 65) -> None:
    path = config.state_root / "configs/channel-operations" / (operation_id + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with database.lock_wait(2), database.locked(config):
                if lifecycle_execution.advance(config, operation_id):
                    return
            time.sleep(0.5)
        with database.lock_wait(2), database.locked(config), store.opened(config) as db:
            operations.update(
                db, operations.get(db, operation_id), "needs_review", "channel_process_not_stopped"
            )
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    args = parser.parse_args()
    config = load_config(args.configuration, args.project_root)
    # Validate the generated identity before constructing any operation file path.
    with store.readonly(config) as db:
        if db is None:
            return 1
        operations.get(db, args.operation_id)
    try:
        run(config, args.operation_id)
        return 0
    except Exception:
        # The durable intent remains visible; never log payloads, credentials or SDK errors.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
