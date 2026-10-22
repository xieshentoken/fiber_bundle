"""Directory scope and inherited read denials, applied before child metadata access."""

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import IntegrityError, InvalidInput
from .inputs import canonical

POLICY = ".knowledge-organizer-policy.json"
PROTECTED = Path.home() / (
    "Library/Mobile Documents/iCloud~md~obsidian/Documents/L2/01-Projects/GF_supply_chain"
)


def prefix(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise InvalidInput("vault_scope_invalid")
    value = value.removesuffix("/**").rstrip("/")
    if value and (
        value.startswith("/")
        or "\\" in value
        or "\0" in value
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise InvalidInput("vault_scope_invalid")
    return value


def inside(path: str, parent: str) -> bool:
    return not parent or path == parent or path.startswith(parent + "/")


@dataclass(frozen=True)
class Scope:
    include: tuple[str, ...] = ("",)
    exclude: tuple[str, ...] = ()
    policy_sha256: str = ""

    def allows(self, relative: str) -> bool:
        return not any(inside(relative, p) for p in self.exclude) and any(
            inside(relative, p) for p in self.include
        )

    def reachable(self, relative: str) -> bool:
        return not any(inside(relative, p) for p in self.exclude) and any(
            inside(relative, p) or inside(p, relative) for p in self.include
        )

    def wire(self) -> dict[str, object]:
        return {
            "include": list(self.include),
            "exclude": list(self.exclude),
            "policy_sha256": self.policy_sha256,
        }


def parse(value: object) -> Scope:
    if not isinstance(value, dict) or set(value) != {"include", "exclude"}:
        raise InvalidInput("vault_scope_invalid")
    groups = []
    for key in ("include", "exclude"):
        items = value[key]
        if (
            not isinstance(items, list)
            or len(items) > 100
            or any(not isinstance(p, str) for p in items)
        ):
            raise InvalidInput("vault_scope_invalid")
        groups.append(tuple(sorted({prefix(p) for p in items})))
    if not groups[0]:
        raise InvalidInput("vault_scope_empty")
    return Scope(groups[0], groups[1])


def effective(root: Path, value: object) -> Scope:
    """Read ancestor policies top-down; a denied root is rejected before its lstat."""
    base = parse(value)
    denied = list(base.exclude)
    if root.name == "L2":
        denied.append("01-Projects/GF_supply_chain")
    evidence = []
    if root == PROTECTED or PROTECTED in root.parents:
        raise InvalidInput("vault_scope_protected")
    if root in PROTECTED.parents:
        denied.append(PROTECTED.relative_to(root).as_posix())
    for parent in [*reversed(root.parents), root]:
        # Reject every path component before inspecting policy metadata beneath it.
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue  # A creation preview may have a missing final component.
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("vault root is not a regular directory")
        from .cloud_files import require_local

        require_local(parent)  # Deny cloud materialization before probing policy children.
        policy = parent / POLICY
        # No listing is performed to find policies. Never follow a policy symlink.
        if policy.is_symlink():
            raise InvalidInput("vault_policy_invalid")
        if not policy.exists():
            continue
        from .cloud_files import require_available
        from .query_routing_files import read

        require_available(policy)
        raw = read(policy, 65536)
        try:
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or data.get("schema_version") != 1
                or not isinstance(data.get("deny_paths"), list)
                or len(data["deny_paths"]) > 100
            ):
                raise ValueError()
            for rule in data["deny_paths"]:
                if (
                    not isinstance(rule, dict)
                    or set(rule) != {"path", "deny"}
                    or not isinstance(rule["deny"], list)
                ):
                    raise ValueError()
                relative = prefix(rule["path"])
                if not relative or any(not isinstance(a, str) for a in rule["deny"]):
                    raise ValueError()
                if not set(rule["deny"]) & {"read", "index", "verify"}:
                    continue
                target = parent / relative
                if root == target or target in root.parents:
                    raise InvalidInput("vault_scope_protected")
                if root in target.parents:
                    denied.append(target.relative_to(root).as_posix())
        except (ValueError, TypeError, KeyError) as exc:
            raise InvalidInput("vault_policy_invalid") from exc
        evidence.append([str(policy), hashlib.sha256(raw).hexdigest()])
    return Scope(
        base.include,
        tuple(sorted(set(denied))),
        hashlib.sha256(canonical(evidence).encode()).hexdigest(),
    )


def for_vault(root: Path, row: sqlite3.Row) -> Scope:
    return effective(root, json.loads(row["scope_json"]))


def entries(
    root: Path,
    scope: Scope,
    *,
    maximum: int = 10000,
    depth: int = 32,
    hidden: bool = False,
    file_filter: Callable[[Path], bool] | None = None,
    batch: bool = False,
) -> Iterator[tuple[Path, os.stat_result]]:
    from . import storage_read_scope

    def scan() -> Iterator[tuple[Path, os.stat_result]]:
        return _entries(
            root,
            scope,
            maximum=maximum,
            depth=depth,
            hidden=hidden,
            file_filter=file_filter,
            batch=batch or storage_read_scope.current.get() is not None,
        )

    if storage_read_scope.current.get() is not None:
        yield from storage_read_scope.read(lambda: list(scan()))
    else:
        yield from scan()


def _entries(
    root: Path,
    scope: Scope,
    *,
    maximum: int = 10000,
    depth: int = 32,
    hidden: bool = False,
    file_filter: Callable[[Path], bool] | None = None,
    batch: bool = False,
) -> Iterator[tuple[Path, os.stat_result]]:
    """Filter names before counting, stat, placeholder checks or recursion."""
    count = 0

    def walk(parent: Path, level: int) -> Iterator[tuple[Path, os.stat_result]]:
        nonlocal count
        if level > depth:
            raise IntegrityError("vault index depth limit exceeded")
        names = []
        with os.scandir(parent) as scanned:
            for child in scanned:
                path = parent / child.name
                relative = path.relative_to(root).as_posix()
                if not scope.reachable(relative) or (
                    child.name.startswith(".")
                    and (
                        not hidden
                        or child.name not in {".fiber-publication.json", ".fiber-analysis.json"}
                    )
                ):
                    continue
                count += 1
                if count > maximum:
                    raise IntegrityError("vault index entry limit exceeded")
                names.append(path)

        def observed() -> Iterator[tuple[Path, os.stat_result]]:
            for path in sorted(names):
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    continue
                if file_filter and not stat.S_ISDIR(info.st_mode) and not file_filter(path):
                    continue
                yield path, info

        children = observed()
        if batch:
            from .cloud_files import require_available_many

            gathered = list(children)
            require_available_many(parent, tuple(p.name for p, _ in gathered))
            children = iter(gathered)
        for path, info in children:
            if not batch:
                from .cloud_files import require_available

                require_available(path)
            yield path, info
            if stat.S_ISDIR(info.st_mode):
                yield from walk(path, level + 1)

    yield from walk(root, 0)
