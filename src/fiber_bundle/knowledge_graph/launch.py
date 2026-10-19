"""One explicitly requested child per frozen job. No backend startup worker or retry timer."""

import os
import subprocess
import sys
import threading
from typing import Any

from .. import database
from ..config import Config, InvalidInput, contained, make_directory
from ..inputs import canonical
from ..query_policy_store import write_private
from ..runtime_environment import child_environment
from . import jobs, policy, store
from .contracts import read_json


def launch(config: Config, cid: str, vid: str, jid: str) -> dict[str, Any]:
    # Authenticated HTTP caller holds the storage lock.
    jobs.authorize(config, cid, vid)
    with store.connection(config) as db:
        if db is None:
            raise InvalidInput("graph_job_unavailable")
        row = store.job(db, jid, cid, vid)
        if row["state"] != "queued":
            return store.view(row)
        prepared = store.decode(row["prepared_json"])
        profile = policy.parse(read_json(row["profile_json"].encode(), 16384))
        jobs._check(config, prepared, profile, _locked=True)
    make_directory(contained(config.state_root, "graph-locks"))
    path = contained(config.state_root, "graph-locks/" + jid + ".config.json")
    if path.exists():
        return {"id": jid, "state": "launch_requested", "reused": True}
    write_private(path, canonical(config.storage_configuration()).encode())
    environment = child_environment()
    for name in ("FIBER_CLOUD_HELPER", "FIBER_CLOUD_HELPER_SHA256"):
        environment.pop(name, None)
    if "HOME" in os.environ:
        environment["HOME"] = os.environ["HOME"]
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fiber_bundle.knowledge_graph.cli",
                "run",
                "--project-root",
                str(config.project_root),
                "--config",
                str(path),
                "--client-id",
                cid,
                "--vault-id",
                vid,
                "--job-id",
                jid,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
    except OSError:
        path.unlink(missing_ok=True)
        raise InvalidInput("graph_launch_failed") from None

    def observe() -> None:
        process.wait()
        with database.locked(config), store.connection(config, write=True) as db:
            assert db is not None
            # Startup failure cannot silently leave an admission eligible for another launch.
            db.execute(
                "UPDATE jobs SET state='interrupted',error_code='graph_child_exited' "
                "WHERE id=? AND state='queued'",
                (jid,),
            )

    threading.Thread(target=observe, daemon=True, name="graph-launch-observer").start()
    return {"id": jid, "state": "launch_requested", "reused": False}
