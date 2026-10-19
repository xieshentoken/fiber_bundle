"""Explicit discovery of installed, offline graph workers; never downloads or enables one."""

import json
import os
import re
import subprocess
import time
from typing import Any
from uuid import uuid4

from ..config import InvalidInput
from . import policy
from .runtime_context import ALGORITHM

# The probe sees only artifacts inside an installed image, without Vaults or credentials.
PROBE = """import json
from pathlib import Path
from dataclasses import asdict
from fiber_bundle.knowledge_graph.contracts import Model
from fiber_bundle.knowledge_graph.runtime_context import verify, ALGORITHM
p=Path('/opt/graph/context.json'); context=json.loads(p.read_bytes())
model=Model(**context['model']); verify(p,Path('/opt/graph/model'),model)
assert context['algorithm']==ALGORITHM
print(json.dumps({'algorithm':ALGORITHM,'model':asdict(model)}))
"""


def detect() -> dict[str, Any]:
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}

    def run(arguments: list[str], timeout: int = 15) -> str:
        completed = subprocess.run(
            ["docker", *arguments],
            env=environment,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        if len(completed.stdout) > 64 * 1024:
            raise InvalidInput("graph_runtime_probe_failed")
        return completed.stdout.decode().strip()

    try:
        endpoint = run(["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
        if re.fullmatch(r"unix:///[^\x00\r\n]+", endpoint) is None:
            raise InvalidInput("graph_local_engine_required")
        local = ["--host", endpoint]
        images = run(
            [*local, "image", "ls", "--filter", "reference=fiber-graph*", "-q", "--no-trunc"]
        )
        checked: list[str] = []
        deadline = time.monotonic() + 55
        for image in dict.fromkeys(images.splitlines()):
            if len(checked) >= 4 or time.monotonic() >= deadline:
                break
            if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
                continue
            checked.append(image)
            name = "fiber-graph-probe-" + str(uuid4())
            try:
                raw = run(
                    [
                        *local,
                        "run",
                        "--rm",
                        "--name",
                        name,
                        "--pull",
                        "never",
                        "--network",
                        "none",
                        "--read-only",
                        "--user",
                        "1000:1000",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--pids-limit",
                        "64",
                        "--memory",
                        "2g",
                        "--cpus",
                        "2",
                        "--tmpfs",
                        "/tmp:rw,nosuid,nodev,size=128m",
                        "--entrypoint",
                        "python",
                        image,
                        "-c",
                        PROBE,
                    ],
                    timeout=max(1, min(20, int(deadline - time.monotonic()))),
                )
                descriptor = json.loads(raw)
                if descriptor["algorithm"] != ALGORITHM:
                    continue
                profile = policy.parse(
                    {
                        "revision": 1,
                        "enabled": True,
                        "image": image,
                        "timeout_seconds": 180,
                        "engine": {
                            "name": "semantica",
                            "version": "0.7.0",
                            "revision": "320761de5d040a54a3220acc563223b4a7ffdc51",
                            "algorithm": ALGORITHM,
                            "model": descriptor["model"],
                            "merge_entities": False,
                            "resolve_conflicts": False,
                            "unknown_relation_endpoint": "reject",
                        },
                    }
                )
                from dataclasses import asdict

                return {
                    "state": "ready",
                    "profile": asdict(profile),
                    "checked": len(checked),
                    "offline": True,
                    "policy_saved": False,
                    "model_calls": 0,
                }
            except (subprocess.SubprocessError, ValueError, KeyError, TypeError, InvalidInput):
                continue
            finally:
                subprocess.run(
                    ["docker", *local, "rm", "--force", name],
                    env=environment,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
        return {
            "state": "unavailable",
            "reason": "graph_compatible_runtime_not_found",
            "checked": len(checked),
            "policy_saved": False,
            "model_calls": 0,
        }
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {
            "state": "unavailable",
            "reason": "graph_local_runtime_unavailable",
            "policy_saved": False,
            "model_calls": 0,
        }
