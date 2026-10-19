"""Explicit finite offline worker; no mounts, credentials, model providers or persistent state."""

import os
import re
import selectors
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Config, InvalidInput
from . import vectors
from .contracts import MAX_OUTPUT_BYTES
from .intake import Prepared, deliver, revalidate
from .results import Result
from .worker_entry import FAILURES


@dataclass(frozen=True)
class Policy:
    image: str
    timeout_seconds: int = 180

    def validate(self) -> None:
        if (
            not isinstance(self.image, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.image) is None
        ):
            raise InvalidInput("graph_fixed_image_required")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 300:
            raise InvalidInput("graph_invalid_timeout")


def _docker(endpoint: str | None, *arguments: str) -> list[str]:
    if endpoint is not None and (
        not endpoint.startswith("unix:///") or any(c in endpoint for c in "\0\n\r")
    ):
        raise InvalidInput("graph_local_engine_required")
    return ["docker", *(["--host", endpoint] if endpoint else []), *arguments]


def _local_endpoint(environment: dict[str, str], *, timeout: float = 15) -> str:
    result = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        check=True,
        timeout=timeout,
        env=environment,
    )
    endpoint = result.stdout.decode("utf-8").strip()
    _docker(endpoint)
    return endpoint


def command(policy: Policy, name: str, *, endpoint: str | None = None) -> list[str]:
    policy.validate()
    if re.fullmatch(r"fiber-graph-[0-9a-f-]{36}", name) is None:
        raise InvalidInput("graph_invalid_container_name")
    return _docker(
        endpoint,
        *[
            "create",
            "--pull",
            "never",
            "--interactive",
            "--name",
            name,
            "--label",
            "fiber-bundle.graph=" + name.removeprefix("fiber-graph-"),
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
            "--ulimit",
            "nofile=256:256",
            "--log-driver",
            "none",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--network",
            "none",
            policy.image,
        ],
    )


def _remove(name: str, endpoint: str | None = None) -> None:
    subprocess.run(
        _docker(endpoint, "rm", "--force", name),
        timeout=15,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    remaining = subprocess.run(
        _docker(endpoint, "ps", "-aq", "--filter", "name=^/" + name + "$"),
        timeout=15,
        capture_output=True,
        check=True,
    )
    if remaining.stdout.strip():
        raise InvalidInput("graph_cleanup_unconfirmed")


def execute_payload(
    policy: Policy,
    task_id: str,
    raw: bytes,
    check: Callable[[], None],
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> bytes:
    """Run a host-authorized derived text payload in the same fixed offline sandbox.

    The caller revalidates source/configuration grants through check before, during,
    and after execution. This boundary never reads files or delivers graph results.
    """
    check()
    policy.validate()
    name = "fiber-graph-" + task_id
    process = None
    created = False
    attempted = False
    deadline = time.monotonic() + policy.timeout_seconds
    endpoint = None
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    try:
        if cancelled():
            raise InvalidInput("graph_cancelled")
        endpoint = _local_endpoint(environment, timeout=min(15, policy.timeout_seconds))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise InvalidInput("graph_worker_timeout")
        if cancelled():
            raise InvalidInput("graph_cancelled")
        attempted = True
        subprocess.run(
            command(policy, name, endpoint=endpoint),
            timeout=min(20, remaining),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        created = True
        check()
        process = subprocess.Popen(
            _docker(endpoint, "start", "--attach", "--interactive", name),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        assert process.stdin and process.stdout and process.stderr
        with selectors.DefaultSelector() as selector:
            for stream, tag, events in (
                (process.stdin, "in", selectors.EVENT_WRITE),
                (process.stdout, "out", selectors.EVENT_READ),
                (process.stderr, "err", selectors.EVENT_READ),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, events, tag)
            sent, observed = 0, time.monotonic()
            output, errors = bytearray(), bytearray()
            while selector.get_map():
                now = time.monotonic()
                if cancelled():
                    raise InvalidInput("graph_cancelled")
                if now >= deadline:
                    raise InvalidInput("graph_worker_timeout")
                if now - observed >= 1:
                    check()
                    observed = now
                for key, _ in selector.select(0.1):
                    if key.data == "in":
                        try:
                            sent += os.write(key.fd, raw[sent : sent + 65536])
                        except BlockingIOError:
                            continue
                        if sent == len(raw):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                    else:
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        target = output if key.data == "out" else errors
                        target.extend(chunk)
                        if len(target) > (MAX_OUTPUT_BYTES if key.data == "out" else 4096):
                            raise InvalidInput("graph_worker_output_limit")
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if process.returncode != 0:
                code = errors.decode("ascii", errors="replace").strip()
                raise InvalidInput(code if code in FAILURES else "graph_worker_failed")
            check()
            return bytes(output)
    except subprocess.TimeoutExpired:
        raise InvalidInput("graph_worker_timeout") from None
    finally:
        try:
            if created:
                _remove(name, endpoint)
            elif attempted:
                assert endpoint is not None
                _remove_owned(task_id, policy.image, endpoint, environment)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()


def _execute(
    config: Config,
    authorization: str | None,
    prepared: Prepared,
    policy: Policy,
    raw: bytes,
    check: Callable[[], None],
    *,
    enabled: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> bytes:
    def validate() -> None:
        check()
        revalidate(config, authorization, prepared, enabled=enabled, allow_rename=True)

    return execute_payload(policy, prepared.request.task_id, raw, validate, cancelled=cancelled)


def run(
    config: Config,
    authorization: str,
    prepared: Prepared,
    policy: Policy,
    *,
    enabled: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
) -> Result:
    output = _execute(
        config,
        authorization,
        prepared,
        policy,
        prepared.request.encode(),
        lambda: None,
        enabled=enabled,
        cancelled=cancelled,
    )
    return deliver(config, authorization, prepared, output, enabled=enabled)


def run_build(
    config: Config,
    prepared: Prepared,
    policy: Policy,
    cache: dict[str, Any],
    *,
    check: Callable[[], None],
    cancelled: Callable[[], bool],
) -> vectors.Build:
    raw = vectors.encode(prepared.request, cache)
    output = _execute(config, None, prepared, policy, raw, check, enabled=True, cancelled=cancelled)
    return vectors.decode_build(output, prepared.request, cache)


def stop_abandoned(jid: str, image: str) -> None:
    """Explicit reconciliation after the host job lease is free; never inspect other containers."""

    from ..note_changes import identifier

    identifier(jid)
    Policy(image).validate()
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    endpoint = _local_endpoint(environment)
    _remove_owned(jid, image, endpoint, environment)


def _remove_owned(jid: str, image: str, endpoint: str, environment: dict[str, str]) -> None:
    import json

    name = "fiber-graph-" + jid
    exists = subprocess.run(
        _docker(endpoint, "ps", "-aq", "--filter", "name=^/" + name + "$"),
        capture_output=True,
        check=True,
        timeout=15,
        env=environment,
    )
    if not exists.stdout.strip():
        return
    inspected = subprocess.run(
        _docker(endpoint, "inspect", name),
        capture_output=True,
        check=True,
        timeout=15,
        env=environment,
    )
    if len(inspected.stdout) > 65536:
        raise InvalidInput("graph_cleanup_binding_invalid")
    data = json.loads(inspected.stdout)[0]
    if (
        data.get("Image") != image
        or data.get("Config", {}).get("Labels", {}).get("fiber-bundle.graph") != jid
    ):
        raise InvalidInput("graph_cleanup_binding_invalid")
    _remove(name, endpoint)


def run_query(
    config: Config,
    prepared: Prepared,
    policy: Policy,
    query: str,
    cache: dict[str, Any],
    *,
    check: Callable[[], None],
) -> list[float]:
    from . import query_protocol

    raw = query_protocol.encode(prepared.request, query, cache)
    output = _execute(config, None, prepared, policy, raw, check, enabled=True)
    return query_protocol.scores(output, prepared.request, query)
