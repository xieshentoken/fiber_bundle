"""Metadata-only ASR dependency probe in a pinned offline container, with no media mounts."""

import json
import re
import subprocess
from typing import Any
from uuid import uuid4

from .media_worker import MediaPolicy

PACKAGES = ("qwen-asr", "accelerate", "torch", "soundfile")
# No package imports, weights, transcriptions, source text, HOME discovery or plugin execution.
PROBE = """
import hashlib,importlib.metadata as m,json,re,sys
result={}
for name in ('qwen-asr','accelerate','torch','soundfile'):
 item={'state':'MISSING','version':None,'metadata_sha256':None}
 try:
  d=m.distribution(name);v=d.version;raw=(d.read_text('METADATA') or '').encode()
  if re.fullmatch(r'[A-Za-z0-9.+_-]{1,64}',v) and 0<len(raw)<=65536:
   item={'state':'FOUND','version':v,'metadata_sha256':hashlib.sha256(raw).hexdigest()}
  else:item['state']='REJECTED'
 except m.PackageNotFoundError:pass
 result[name]=item
print(json.dumps({'schema':'fiber-asr-dependencies/v1','python_version':sys.version.split()[0],
 'packages':result},sort_keys=True))
"""


def validate(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "python_version", "packages"}:
        raise ValueError("ASR metadata fields")
    if (
        value["schema"] != "fiber-asr-dependencies/v1"
        or not isinstance(value["python_version"], str)
        or not re.fullmatch(r"\d+\.\d+\.\d+", value["python_version"])
    ):
        raise ValueError("ASR metadata version")
    packages = value["packages"]
    if not isinstance(packages, dict) or set(packages) != set(PACKAGES):
        raise ValueError("ASR metadata packages")
    for item in packages.values():
        if not isinstance(item, dict) or set(item) != {"state", "version", "metadata_sha256"}:
            raise ValueError("ASR metadata package fields")
        if not isinstance(item["state"], str):
            raise ValueError("ASR metadata package state")
        if item["state"] == "FOUND":
            if (
                not isinstance(item["version"], str)
                or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,64}", item["version"])
                or not isinstance(item["metadata_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["metadata_sha256"])
            ):
                raise ValueError("ASR package version or hash")
        elif item["state"] not in {"MISSING", "REJECTED"} or (
            item["version"] is not None or item["metadata_sha256"] is not None
        ):
            raise ValueError("ASR metadata package state")
    return value


def inspect(image: str) -> dict[str, Any]:
    MediaPolicy(image).validate()
    identifier = str(uuid4())
    name = "fiber-asr-probe-" + identifier
    created = False
    result: dict[str, Any] = {"state": "BLOCKED", "error_code": "asr_dependency_probe_failed"}
    try:
        subprocess.run(
            [
                "docker",
                "create",
                "--pull",
                "never",
                "--name",
                name,
                "--label",
                "fiber-bundle.asr-probe=" + identifier,
                "--network",
                "none",
                "--read-only",
                "--user",
                "1000:1000",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--memory-swap",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "32",
                "--log-driver",
                "none",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
                "--entrypoint",
                "python",
                image,
                "-I",
                "-c",
                PROBE,
            ],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        created = True
        completed = subprocess.run(
            ["docker", "start", "--attach", name],
            check=True,
            timeout=15,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if len(completed.stdout) > 8192:
            raise ValueError("ASR probe size")
        value = validate(json.loads(completed.stdout))
        result = {"state": "INSPECTED", "metadata": value, "error_code": None}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
        pass
    finally:
        if created:
            try:
                subprocess.run(
                    ["docker", "rm", "--force", name],
                    check=True,
                    timeout=15,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                result = {"state": "BLOCKED", "error_code": "asr_probe_stop_failed"}
    return {
        **result,
        "image": image,
        "probe_id": identifier,
        "probe_kind": "installed_metadata_only",
        "media_mounts": 0,
        "model_imports": 0,
        "model_calls": 0,
        "weights_downloaded": False,
        "runtime_qualified": False,
        "resource_acceptance": "NOT_RUN",
    }
