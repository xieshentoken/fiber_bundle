"""Explicit independent backend start/run/status; never initialize or migrate a database."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from . import backend_lifecycle, database
from .api import create_app
from .config import Config, StorageError, load_config
from .http_guard import Settings
from .runtime_environment import child_environment


def environment() -> dict[str, str]:
    return child_environment()


async def serve(
    config: Config, settings: Settings, control: backend_lifecycle.BackendControl
) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, settings, lifecycle=control),
            host=settings.host,
            port=settings.port,
            access_log=False,
            server_header=False,
            proxy_headers=False,
        )
    )

    async def watch() -> None:
        while not server.started and not server.should_exit:
            await asyncio.sleep(0.05)
        if server.started:
            control.record("running")
            try:
                control.recovery = {
                    "state": "checked",
                    **await asyncio.to_thread(backend_lifecycle.resume_safe, config),
                }
            except Exception as exc:
                # Keep the stop listener alive even when a reserved admission cannot resume.
                # Native ledgers retain its state; neither attempts nor unknown IO are replayed.
                control.recovery = {"state": "blocked", "error_type": type(exc).__name__}
            control.record("running")
        while not server.should_exit:
            if control.stop_event.is_set():
                server.should_exit = True
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        control.record("stopped")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("start", "run", "status"))
    p.add_argument("--project-root", type=Path, default=Path.cwd())
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--session-ttl", type=int, default=3600)
    args = p.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        path = args.config if args.config.is_absolute() else root / args.config
        config = load_config(path, root)
        settings = Settings(port=args.port, session_ttl=args.session_ttl)
        settings.validate()
        db = database.connect(config.state_root)
        db.close()
        if args.command == "status":
            print(json.dumps(backend_lifecycle.observe(config)))
        elif args.command == "start":
            before = backend_lifecycle.observe(config)
            if before["state"] in ("starting", "running", "stopping"):
                if before.get("origin") != settings.origin:
                    raise StorageError("backend_origin_conflict")
                print(json.dumps(before))
                return 0
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "fiber_bundle.backend_cli",
                    "run",
                    "--project-root",
                    str(root),
                    "--config",
                    str(path),
                    "--port",
                    str(args.port),
                    "--session-ttl",
                    str(args.session_ttl),
                ],
                env=environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(100):
                state = backend_lifecycle.observe(config)
                if state["state"] == "running":
                    if state.get("origin") != settings.origin:
                        raise StorageError("backend_origin_conflict")
                    print(json.dumps(state))
                    return 0
                if child.poll() is not None:
                    break
                time.sleep(0.05)
            raise StorageError("backend_start_failed")
        else:
            with backend_lifecycle.process_lock(config):
                control = backend_lifecycle.BackendControl(config, settings.origin)
                control.record("starting")
                asyncio.run(serve(config, settings, control))
    except (StorageError, OSError, ValueError) as exc:
        print(json.dumps({"state": "error", "error_type": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
