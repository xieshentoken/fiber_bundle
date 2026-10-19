"""Finite independent video runner launch; persisted intent prevents automatic replay."""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import video_tasks
from .config import Config, contained, make_directory
from .inputs import canonical
from .query_policy_store import write_private
from .runtime_environment import child_environment


def launch(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> dict[str, Any]:
    row = video_tasks.get(config, db, cid, identity)
    if row["state"] != "queued":
        return video_tasks.project(row)
    value = json.loads(row["value"])
    video_tasks.update(db, identity, "launching", value)
    folder = contained(config.state_root, "configs/video-runs")
    make_directory(folder)
    path = folder / (identity + ".json")
    write_private(
        path, canonical({"storage": config.storage_configuration(), "client_id": cid}).encode()
    )
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fiber_bundle.video_launch",
                "--project-root",
                str(config.project_root),
                "--configuration",
                str(path),
                "--task",
                identity,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except OSError:
        video_tasks.update(
            db, identity, "interrupted", {**value, "error_code": "video_launch_uncertain"}
        )
        raise
    return video_tasks.project(video_tasks.get(config, db, cid, identity))


def launch_link(config: Config, db: sqlite3.Connection, cid: str, identity: str) -> dict[str, Any]:
    from . import video_links

    row = video_links.get(config, db, cid, identity)
    from .executor_container import run_lock

    with run_lock(config, identity):
        video_links.update(db, row, "running")
        folder = contained(config.state_root, "configs/video-runs")
        make_directory(folder)
        path = folder / (identity + ".json")
        write_private(
            path, canonical({"storage": config.storage_configuration(), "client_id": cid}).encode()
        )
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "fiber_bundle.video_launch",
                    "--link",
                    "--project-root",
                    str(config.project_root),
                    "--configuration",
                    str(path),
                    "--task",
                    identity,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_environment(),
                start_new_session=True,
            )
        except OSError:
            video_links.update(db, row, "interrupted")
            raise
    return video_links.wire(video_links.get(config, db, cid, identity))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--link", action="store_true")
    args = parser.parse_args()
    from .query_routing_files import read
    from .video_runner import run

    try:
        data = json.loads(read(args.configuration, 65536))
        storage = data["storage"]
        config = Config(
            args.project_root,
            state_relative=storage["state_root"],
            vaults=tuple(storage["vaults"].items()),
            vault_roots=tuple(storage.get("vault_roots", {}).items()),
        )
        config.validate()
        if args.link:
            from .video_links import run as run_link

            run_link(config, data["client_id"], args.task)
        else:
            run(config, data["client_id"], args.task)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
