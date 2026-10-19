"""Reverse exclusive moves and reuse retained original inodes; never overwrite user edits."""

import os
from pathlib import Path
from typing import Any

from .. import file_exchange, publication_file_io, vault_directories
from ..config import IntegrityError
from ..objects import digest


def observe(root: Path, path: str) -> Any:
    try:
        raw, key = publication_file_io.read(root, path)
        return {"sha256": digest(raw), "file_key": key}
    except FileNotFoundError:
        return None


def rename(root: Path, scope: Any, source: str, target: str) -> None:
    with vault_directories.opened_parent(
        root,
        scope,
        str(Path(source).parent).replace(".", "")
        if "/" not in source
        else source.rsplit("/", 1)[0],
    ) as (_, src):
        with vault_directories.opened_parent(
            root, scope, target.rsplit("/", 1)[0] if "/" in target else ""
        ) as (_, dst):
            file_exchange.rename(src, Path(source).name, dst, Path(target).name, exchange=False)


def check(root: Path, scope: Any, journal: Any) -> None:
    for path, evidence in journal["files"].items():
        if evidence["before"] == evidence["after"]:
            continue
        if not scope.allows(path):
            raise IntegrityError("maintenance_undo_scope_changed")
        if observe(root, path) != evidence["after"]:
            raise IntegrityError("maintenance_undo_file_conflict")
    for move in journal["moves"]:
        if move.get("restored") or not move["files"]:
            continue
        source, target = move["source"], move["target"]
        if (root / source).exists():
            raise IntegrityError("maintenance_undo_move_conflict")
        for old, expected in move["files"].items():
            path = target + old[len(source) :]
            if not scope.allows(old) or not scope.allows(path) or observe(root, path) != expected:
                raise IntegrityError("maintenance_undo_move_conflict")
        if move["package"]:
            contents = publication_file_io.tree(root, target, scope)
            if set(contents) != {p[len(source) + 1 :] for p in move["files"]}:
                raise IntegrityError("maintenance_undo_move_conflict")


def restore(config: Any, root: Path, scope: Any, journal: Any, save: Any) -> bool:
    remaining = 8  # Scheduling slice, not a limit on task size.
    for move in reversed(journal["moves"]):
        if move.get("restored") or not move["files"]:
            continue
        if any(
            not scope.allows(p) or not scope.allows(move["target"] + p[len(move["source"]) :])
            for p in move["files"]
        ):
            raise IntegrityError("maintenance_undo_scope_changed")
        if not (root / move["source"]).exists():
            rename(root, scope, move["target"], move["source"])
        else:
            if (root / move["target"]).exists():
                raise IntegrityError("maintenance_undo_move_conflict")
            for path, expected in move["files"].items():
                if observe(root, path) != expected:
                    raise IntegrityError("maintenance_undo_move_conflict")
        move["restored"] = True
        save()
        remaining -= 1
        if not remaining:
            return False
    for path, evidence in journal["files"].items():
        if evidence.get("restored"):
            continue
        before, after = evidence["before"], evidence["after"]
        if before == after:
            evidence["restored"] = True
            continue
        if not scope.allows(path):
            raise IntegrityError("maintenance_undo_scope_changed")
        # A moved source is present again at its original path now.
        moved = next((m for m in journal["moves"] if path in m["files"]), None)
        expected = moved["files"][path] if moved else after
        current = observe(root, path)
        if current == before:
            evidence["restored"] = True
            save()
            continue
        if current != expected:
            raise IntegrityError("maintenance_undo_file_conflict")
        if before is None:
            if current:
                home = config.state_root / "maintenance-undo-files" / journal["id"]
                home.mkdir(parents=True, exist_ok=True, mode=0o700)
                src = file_exchange.parent_fd(root, path)
                dst = file_exchange.parent_fd(home, digest(path.encode()))
                try:
                    file_exchange.rename(
                        src, Path(path).name, dst, digest(path.encode()), exchange=False
                    )
                finally:
                    os.close(src)
                    os.close(dst)
        else:
            backup = evidence.get("backup")
            if not backup:
                raise IntegrityError("maintenance_undo_original_unavailable")
            home, name = Path(backup).parent, Path(backup).name
            if observe(home, name) != before:
                raise IntegrityError("maintenance_undo_original_unavailable")
            src, dst = file_exchange.parent_fd(home, name), file_exchange.parent_fd(root, path)
            try:
                file_exchange.rename(src, name, dst, Path(path).name, exchange=True)
                # A concurrent Obsidian edit during the exchange belongs in the
                # vault. Restore it immediately while our original is still there.
                if observe(home, name) != expected:
                    file_exchange.rename(src, name, dst, Path(path).name, exchange=True)
                    raise IntegrityError("maintenance_undo_file_conflict")
            finally:
                os.close(src)
                os.close(dst)
        evidence["restored"] = True
        save()
        remaining -= 1
        if not remaining:
            return False
    return True
