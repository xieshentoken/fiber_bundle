"""Content-pinned authoring helpers, including the bytes executed inside the container."""

import os
import re
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from .config import Config, InvalidInput, contained, make_directory, sync_directory
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private

LEGACY = "27fe6e72ef3b733d1b1d3dc1b691a43f69fc656335a64769cc309a3b5fa956b4"

FILES = (
    "expert_authoring_entry.py",
    "expert_authoring_tools.py",
    "expert_worker_entry.py",
    "processing_container.py",
    "expert_authoring_units.py",
    "expert_proposals.py",
)


def _files(root: Path) -> dict[str, bytes]:
    result = {}
    for name in FILES:
        path = root / name
        if path.is_symlink() or path.resolve() != path:
            raise InvalidInput("expert_authoring_tools_changed")
        result[name] = path.read_bytes()
    return result


def _manifest(files: dict[str, bytes]) -> bytes:
    return canonical({n: digest(raw) for n, raw in files.items()}).encode()


def capture(config: Config) -> str:
    files = _files(Path(__file__).parent)
    manifest = _manifest(files)
    identity = digest(manifest)
    root = contained(config.state_root, "expert-tool-snapshots/" + identity)
    if not root.exists():
        make_directory(root.parent)
        temporary = root.parent / ("stage-" + str(uuid4()))
        make_directory(temporary)
        try:
            for name, raw in {**files, "files.json": manifest}.items():
                write_private(temporary / name, raw)
            try:
                os.rename(temporary, root)
                sync_directory(root.parent)
            except OSError:
                if not root.is_dir():
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if (
        _manifest(_files(root)) != (root / "files.json").read_bytes()
        or digest((root / "files.json").read_bytes()) != identity
    ):
        raise InvalidInput("expert_authoring_tools_changed")
    return identity


def verify(config: Config, identity: str) -> Path:
    if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
        raise InvalidInput("expert_authoring_tools_changed")
    here = Path(__file__).parent
    if digest(_manifest(_files(here))) == identity:
        return here
    roots = (
        contained(config.state_root, "expert-tool-snapshots/" + identity),
        here / "expert_tool_snapshots" / identity,
    )
    for root in roots:
        if not root.exists():
            continue
        try:
            manifest = (root / "files.json").read_bytes()
            if digest(manifest) != identity or _manifest(_files(root)) != manifest:
                raise ValueError()
            return root
        except (OSError, ValueError):
            raise InvalidInput("expert_authoring_tools_changed") from None
    raise InvalidInput("expert_authoring_tools_changed")


def _load(root: Path, name: str, raw: bytes) -> Any:
    # Relative imports retain the shared database/config exception identities. Pinned
    # helper dependencies are explicitly rebound below, rather than imported current.
    module = ModuleType("fiber_bundle._pinned_" + name)
    module.__package__ = "fiber_bundle"
    module.__file__ = str(root / (name + ".py"))
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return module


def load(config: Config, identity: str) -> tuple[Any, Any]:
    root = verify(config, identity)
    here = Path(__file__).parent
    files = _files(root)
    if digest(_manifest(files)) != identity:
        raise InvalidInput("expert_authoring_tools_changed")
    tools = _load(root, "expert_authoring_tools", files["expert_authoring_tools.py"])
    units = _load(root, "expert_authoring_units", files["expert_authoring_units.py"])
    proposals = _load(root, "expert_proposals", files["expert_proposals.py"])
    container = _load(root, "processing_container", files["processing_container.py"])
    # The base package supplies shared dependencies; six file overlays supply exactly
    # the pinned compiler entry and its pinned imports inside the read-only sandbox.
    container.__file__ = str(here / "processing_container.py")
    original_run = container.run

    def run(*args: Any, **kwargs: Any) -> None:
        verify(config, identity)
        overlays = []
        for name in FILES:
            overlays.extend(
                [
                    "--mount",
                    "type=bind,source="
                    + str(root / name)
                    + ",target=/opt/fiber/fiber_bundle/"
                    + name
                    + ",readonly",
                ]
            )
        existing = kwargs.get("skill_mounts") or []
        targets = {"/opt/fiber/fiber_bundle/" + name for name in FILES}
        kept = []
        for index in range(0, len(existing), 2):
            option, binding = existing[index : index + 2]
            target = next(
                (
                    part.split("=", 1)[1]
                    for part in binding.split(",")
                    if part.startswith(("target=", "dst=", "destination="))
                ),
                None,
            )
            if target not in targets:
                kept.extend([option, binding])
        kwargs["skill_mounts"] = [*kept, *overlays]
        original_run(*args, **kwargs)

    container.run = run
    tools.processing_container = container
    units.validate = proposals.validate
    units.fragments = proposals.fragments
    units.combine = proposals.combine
    return tools, units
