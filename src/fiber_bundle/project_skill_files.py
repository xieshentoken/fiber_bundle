"""Project-only Skill discovery and content-addressed snapshots; no code execution."""

import ast
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import Config, InvalidInput, contained, make_directory, sync_directory
from .inputs import canonical
from .objects import digest
from .project_skill_contract import SEARCH_RUNTIME_SHA256

AREAS = ("scripts", "references", "assets", "agents")
REQUIRED = (
    "SKILL.md",
    "requirements-compiler.txt",
    "scripts/fake_expert.py",
    "scripts/compiler_version.py",
    "assets/schemas/semantic-draft.schema.json",
)


def source(config: Config) -> Path:
    return contained(config.project_root, "skills/fake-expert")


def read_tree(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or "," in str(root):
        raise InvalidInput("project_skill_directory_invalid")
    files: dict[str, bytes] = {}
    aliases = set()
    total = 0
    candidates = [root / "SKILL.md", root / "requirements-compiler.txt"]
    extras = root / "requirements-parser-extras.txt"
    if extras.exists() or extras.is_symlink():
        candidates.append(extras)
    for area in AREAS:
        folder = root / area
        if folder.is_symlink():
            raise InvalidInput("project_skill_path_invalid")
        if not folder.exists():
            continue
        for parent, directories, names in os.walk(folder, followlinks=False):
            for name in directories:
                if (Path(parent) / name).is_symlink():
                    raise InvalidInput("project_skill_path_invalid")
            directories[:] = [n for n in directories if n != "__pycache__"]
            candidates.extend(Path(parent) / n for n in names if not n.endswith(".pyc"))
    for path in sorted(candidates):
        name = path.relative_to(root).as_posix()
        contained(root, name)
        if not path.is_file() or path.stat().st_nlink != 1 or path.resolve() != path:
            raise InvalidInput("project_skill_path_invalid")
        alias = unicodedata.normalize("NFC", name).casefold()
        if alias in aliases or len(files) >= 2000 or path.stat().st_size > 32 * 1024 * 1024:
            raise InvalidInput("project_skill_files_invalid")
        raw = path.read_bytes()
        total += len(raw)
        if total > 32 * 1024 * 1024:
            raise InvalidInput("project_skill_files_invalid")
        aliases.add(alias)
        files[name] = raw
    if any(name not in files for name in REQUIRED):
        raise InvalidInput("project_skill_files_missing")
    try:
        text = files["SKILL.md"].decode()
        header = text.split("---", 2)
        if (
            len(header) != 3
            or header[0].strip()
            or not re.search(r"(?m)^name:\s*fake-expert\s*$", header[1])
            or not re.search(r"(?m)^description:\s*\S", header[1])
        ):
            raise ValueError()
        values = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(files["scripts/compiler_version.py"]).body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"RELEASE_VERSION", "UX_CLI_PROTOCOL"}
        }
        if values != {"RELEASE_VERSION": "1.2.0", "UX_CLI_PROTOCOL": "fake-expert-ux-cli-v0.2"}:
            raise ValueError()
    except (ValueError, SyntaxError, UnicodeError):
        raise InvalidInput("project_skill_protocol_incompatible") from None
    return files


def features(files: dict[str, bytes]) -> dict[str, str]:
    values = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(files["scripts/compiler_version.py"]).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id
        in {"DIRECT_REFERENCE_COMPILER_VERSION", "REFERENCE_SEARCH_RUNTIME_VERSION"}
    }
    direct = values.get("DIRECT_REFERENCE_COMPILER_VERSION", "1.1.0-direct-reference")
    search = values.get("REFERENCE_SEARCH_RUNTIME_VERSION", "portable-reference-v0.1")
    if (
        not isinstance(direct, str)
        or not isinstance(search, str)
        or (direct, search)
        not in {
            ("1.1.0-direct-reference", "portable-reference-v0.1"),
            ("1.4.0-direct-reference", "reference-search-bm25-v0.3"),
        }
    ):
        raise InvalidInput("project_skill_protocol_incompatible")
    if (
        direct == "1.4.0-direct-reference"
        and digest(files.get("scripts/reference_search.py", b"")) != SEARCH_RUNTIME_SHA256
    ):
        raise InvalidInput("project_skill_protocol_incompatible")
    return {
        "direct_compiler": direct,
        "search_runtime": search,
        "figures": "index+images" if direct == "1.4.0-direct-reference" else "none",
    }


def contents(files: dict[str, bytes]) -> tuple[dict[str, Any], bytes]:
    boundary = Path(__file__).with_name("executor") / "expert-processing.md"
    pi = (
        files["SKILL.md"]
        + (
            b"\n\n# Fiber task boundary\n"
            b"The complete compiler Skill above is available at /task/skills/fake-expert. "
            b"Resolve its references there. This Pi process authors proposals only. "
            b"The host runs doctor/plan/compile and publishes; "
            b"do not run commands or grant review.\n"
        )
        + boundary.read_bytes()
    )
    manifest = {name: digest(raw) for name, raw in files.items()}
    return {
        "schema": "fiber-skill-snapshot/v1",
        "id": "fake-expert",
        "release": "1.2.0",
        "protocol": "fake-expert-ux-cli-v0.2",
        "features": features(files),
        "files": manifest,
        "compiler_sha256": digest(canonical(manifest).encode()),
        "pi_sha256": digest(pi),
    }, pi


def snapshot_path(config: Config, identity: str) -> Path:
    if re.fullmatch(r"[a-f0-9]{64}", identity) is None:
        raise InvalidInput("project_skill_binding_invalid")
    return contained(config.state_root, "project-skills/snapshots/" + identity)


def capture(config: Config) -> dict[str, Any]:
    files = read_tree(source(config))
    metadata, pi = contents(files)
    identity = digest(canonical(metadata).encode())
    target = snapshot_path(config, identity)
    if not target.exists():
        parent = target.parent
        make_directory(parent)
        temporary = parent / ("stage-" + str(uuid4()))
        temporary.mkdir(mode=0o700)
        try:
            for name, raw in files.items():
                path = temporary / "skill" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o444)
            for name, raw in {
                "snapshot.json": canonical(metadata).encode(),
                "skill-files.json": canonical(metadata["files"]).encode(),
                "pi.md": pi,
            }.items():
                (temporary / name).write_bytes(raw)
                (temporary / name).chmod(0o444)
            if read_tree(source(config)) != files:
                raise InvalidInput("project_skill_source_changed")
            os.rename(temporary, target)
            sync_directory(parent)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return verify(config, identity)


def verify(config: Config, identity: str) -> dict[str, Any]:
    root = snapshot_path(config, identity)
    try:
        raw = contained(root, "snapshot.json").read_bytes()
        metadata = json.loads(raw)
        if digest(raw) != identity or canonical(metadata).encode() != raw:
            raise ValueError()
        files = read_tree(contained(root, "skill"))
        if {n: digest(b) for n, b in files.items()} != metadata["files"]:
            raise ValueError()
        if digest(contained(root, "pi.md").read_bytes()) != metadata["pi_sha256"]:
            raise ValueError()
        manifest = contained(root, "skill-files.json").read_bytes()
        if (
            canonical(metadata["files"]).encode() != manifest
            or digest(manifest) != metadata["compiler_sha256"]
        ):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise InvalidInput("project_skill_snapshot_changed") from None
    return {**metadata, "snapshot_sha256": identity}
