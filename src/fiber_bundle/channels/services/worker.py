"""One independent, cooperatively stopped worker with an exclusive role lease."""

import argparse
import fcntl
import json
import os
import signal
import time
from pathlib import Path
from threading import Event, Thread

from ... import auth, database
from ...config import InvalidInput, load_config
from ...query_policy_store import write_private
from .. import store
from . import control, execution, policy


def run(root: Path, configuration: Path, nonce: str) -> None:
    launch = json.loads(configuration.read_text())
    if launch["nonce"] != nonce:
        raise InvalidInput("channel_service_launch_withdrawn")
    storage = configuration.parent / ("storage-" + nonce + ".json")
    write_private(storage, json.dumps(launch["storage"]).encode())
    config = load_config(storage, root)
    identity, role = launch["channel_id"], launch["role"]
    path = control.home(config, identity, role)
    if configuration != path / "launch.json":
        raise InvalidInput("channel_service_launch_invalid")
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    fd = os.open(path / "lease.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if json.loads(configuration.read_text())["nonce"] != nonce:
            return
        execution.recover(config, identity, role)
        state = {
            "state": "starting",
            "pid": os.getpid(),
            "generation": launch["generation"],
            "image": launch["image"],
            "error": None,
        }

        def pulse() -> None:
            while not stopped.wait(1):
                write_private(
                    path / "status.json",
                    json.dumps({**state, "heartbeat_at": time.time()}).encode(),
                )

        heartbeat = Thread(target=pulse, daemon=True)
        heartbeat.start()
        try:
            while not stopped.is_set():
                desired = policy.get(config, identity, role)
                if (
                    not desired
                    or not desired["enabled"]
                    or desired["revision"] != launch["generation"]
                ):
                    break
                with store.readonly(config) as db:
                    if db is None:
                        raise InvalidInput("channel_service_binding_unavailable")
                    entry = store.get(db, identity)
                if entry["state"] in {"removed", "unbinding", "removing", "unpaired"}:
                    state.update(state="blocked", error="channel_service_binding_unavailable")
                    break
                state.update(state="working", error=None)
                try:
                    with database.lock_wait(1):
                        execution.tick(config, identity, role)
                    state.update(state="idle")
                except database.LockTimeout:
                    state.update(state="waiting_storage", error=None)
                except (InvalidInput, auth.Denied) as error:
                    state.update(
                        state="blocked",
                        error=str(error)
                        if isinstance(error, InvalidInput)
                        else "permission_denied",
                    )
                except Exception as error:
                    # Unknown work is retained, never reset to runnable by the service.
                    state.update(state="blocked", error="channel_service_" + type(error).__name__)
                stopped.wait(1)
        finally:
            stopped.set()
            heartbeat.join(timeout=2)
            state.update(state="stopped")
            write_private(
                path / "status.json", json.dumps({**state, "heartbeat_at": time.time()}).encode()
            )
    finally:
        os.close(fd)
        storage.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--configuration", required=True, type=Path)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    run(args.project_root, args.configuration, args.nonce)


if __name__ == "__main__":
    main()
