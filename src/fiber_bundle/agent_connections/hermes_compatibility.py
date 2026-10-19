"""Read-only installation inspection. Static compatibility never enables a runtime."""

import ast
import hashlib
import os
import stat
import tomllib
from pathlib import Path
from typing import Any

from ..config import InvalidInput
from ..inputs import canonical

FILES = (
    "pyproject.toml",
    "run_agent.py",
    "agent/agent_init.py",
    "agent/turn_context.py",
    "agent/conversation_loop.py",
    "hermes_cli/plugins.py",
    "hermes_cli/lifecycle.py",
    "tools/mcp_tool.py",
)
REQUIRED_ARGUMENTS = frozenset(
    {
        "base_url",
        "api_key",
        "model",
        "provider",
        "api_mode",
        "max_iterations",
        "max_tokens",
        "enabled_toolsets",
        "disabled_toolsets",
        "skip_memory",
        "skip_context_files",
        "load_soul_identity",
        "session_db",
        "fallback_model",
        "save_trajectories",
    }
)


def _read(root: Path, relative: str) -> bytes:
    path = root / relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise InvalidInput("agent_hermes_source_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 2097152:
            raise InvalidInput("agent_hermes_source_size")
        raw = stream.read(2097153)
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(raw) != info.st_size:
            raise InvalidInput("agent_hermes_source_changed")
        return raw


def inspect(root: Path) -> dict[str, Any]:
    """Read selected public sources only. Do not import Hermes, config or credentials."""
    root = root.absolute()
    if root.resolve() != root or not root.is_dir():
        raise InvalidInput("agent_hermes_installation_path")
    try:
        sources = {name: _read(root, name) for name in FILES}
        package = tomllib.loads(sources["pyproject.toml"].decode())["project"]
        tree = ast.parse(sources["run_agent.py"])
        agent = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AIAgent")
        methods = {n.name: n for n in agent.body if isinstance(n, ast.FunctionDef)}
        constructor = methods["__init__"]
        arguments = {a.arg for a in constructor.args.args + constructor.args.kwonlyargs}
        turn = methods["run_conversation"]
        turn_arguments = {a.arg for a in turn.args.args + turn.args.kwonlyargs}
        compatible = (
            package["name"] == "hermes-agent"
            and isinstance(package["version"], str)
            and REQUIRED_ARGUMENTS <= arguments
            and {"user_message", "system_message", "conversation_history"} <= turn_arguments
            and "close" in methods
        )
    except (OSError, ValueError, KeyError, StopIteration, TypeError, SyntaxError):
        raise InvalidInput("agent_hermes_installation_unreadable") from None
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
    return {
        "state": "candidate" if compatible else "incompatible",
        "version": package.get("version"),
        "source_sha256": hashlib.sha256(canonical(hashes).encode()).hexdigest(),
        "source_files": hashes,
        "fingerprint_scope": "selected_public_sources_only",
        "entrypoint": "run_agent.AIAgent.run_conversation",
        "strategy": "retrieve_before_constructing_agent",
        "runtime_verified": False,
        "configuration_read": False,
        "model_called": False,
    }
