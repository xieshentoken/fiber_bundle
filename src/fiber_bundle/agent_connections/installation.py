"""Inspect the known local Hermes installation, never load a personal profile."""

from pathlib import Path
from typing import Any

from ..config import InvalidInput
from ..query_routing_files import read
from .hermes_compatibility import inspect


def current() -> dict[str, Any]:
    root = (Path.home() / ".hermes/hermes-agent").resolve()
    info = inspect(root)
    python = root / "venv/bin/python"
    if info["state"] != "candidate" or info["version"] != "0.19.1" or not python.is_file():
        raise InvalidInput("agent_hermes_incompatible")
    dependencies = {}
    for name, version in (("mcp", "1.26.0"), ("openai", "2.24.0"), ("httpx", "0.28.1")):
        paths = list(
            root.glob("venv/lib/python3.11/site-packages/" + name + "-*.dist-info/METADATA")
        )
        if len(paths) != 1:
            raise InvalidInput("agent_hermes_dependency_changed")
        metadata = read(paths[0], 262144).decode("utf-8")
        if ("Version: " + version) not in metadata.splitlines():
            raise InvalidInput("agent_hermes_dependency_changed")
        dependencies[name] = version
    return {**info, "root": str(root), "python": str(python), "dependencies": dependencies}
