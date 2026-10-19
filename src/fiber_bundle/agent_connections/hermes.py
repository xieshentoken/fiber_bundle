"""Invoke the actual public Hermes entrypoint in an isolated owned process."""

import json
import subprocess
from pathlib import Path
from typing import Any

from ..config import InvalidInput
from ..inputs import canonical
from . import installation
from .turn import PreparedTurn


def generate(
    prepared: PreparedTurn,
    applied: dict[str, Any],
    model: dict[str, Any],
    home: Path,
    output: Path,
    timeout: int,
) -> dict[str, Any]:
    current = installation.current()
    if (
        current != applied["hermes"]
        or model["configuration_sha256"] != applied["model_configuration_sha256"]
    ):
        raise InvalidInput("agent_runtime_changed")
    limits = json.loads(prepared.context_json)["configuration"]["limits"]
    payload = {
        "root": current["root"],
        "home": str(home),
        "output": str(output),
        "model": model,
        "timeout_seconds": timeout,
        "system": prepared.system_message,
        "user": prepared.user_message,
        "max_tokens": min(limits["output_tokens"], model["max_output_tokens"]),
        "session_id": json.loads(prepared.context_json)["connection_id"],
    }
    if model.get("processing_location") == "cloud":
        payload["system"] += (
            " Output one raw JSON object, without Markdown fences or any preamble. "
            "The evidence is in the JSON user message. "
            "Source IDs such as bracketed numbers belong ONLY in claims[].citations; "
            "never write a source ID in claims[].text or model_supplement. "
            "Refer to conflicting records by their vault names or recorded values in prose."
        )
    child = subprocess.Popen(
        [current["python"], "-I", "-B", str(Path(__file__).with_name("hermes_entry.py"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HERMES_HOME": str(home)},
        cwd=home,
    )
    try:
        child.communicate(canonical(payload).encode(), timeout=timeout)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
    if child.returncode:
        raise InvalidInput("agent_generation_failed")
    from ..query_routing_files import read

    result = json.loads(read(output, 262144))
    if not isinstance(result, dict):
        raise InvalidInput("agent_generation_invalid")
    if result["provider_calls"] != 1 or not isinstance(result["answer"], str):
        raise InvalidInput("agent_generation_invalid")
    return result


def diagnostic(path: Path) -> dict[str, Any]:
    """Safe numeric/code projection only; never return provider messages or partial answers."""
    from . import files

    try:
        value = files.private_json(path, 262144)
        if not isinstance(value, dict) or value.get("error_code") != "agent_hermes_failed":
            return {"usage_state": "unknown"}
        data = value.get("diagnostics", {})
        result: dict[str, Any] = {"usage_state": "unknown"}
        for key in ("provider_calls", "metadata_requests", "input_bytes"):
            if type(data.get(key)) is int and 0 <= data[key] <= 262144:
                result[key] = data[key]
        if data.get("provider_error") in {
            "agent_input_budget",
            "agent_provider_http_400",
            "agent_provider_http_401",
            "agent_provider_http_403",
            "agent_provider_http_408",
            "agent_provider_http_413",
            "agent_provider_http_429",
            "agent_provider_http_500",
            "agent_provider_http_502",
            "agent_provider_http_503",
            "agent_provider_http_504",
            "agent_provider_http_507",
            "URLError",
            "TimeoutError",
        }:
            result["provider_error"] = data["provider_error"]
        return result
    except (OSError, ValueError, InvalidInput):
        return {"usage_state": "unknown"}
