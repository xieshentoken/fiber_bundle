"""Fresh, bounded link names with task-local reuse and directory-race diagnostics."""

import os
import stat
from pathlib import Path
from typing import Any

from . import cloud_observation, vault_scope
from .cloud_files import require_local
from .config import IntegrityError
from .note_read_session import signature, stat_signature

CODE = "placement_link_sources_changed"


class _IncompleteCensus(Exception):
    """Let a caller with an original journal proof own any incomplete retry."""

    def __init__(self, error: cloud_observation.ObservationError) -> None:
        self.error = error


def _children(root: Path, scope: Any, omissions: Any, directory: str) -> set[tuple[str, bool]]:
    observed = set()
    with os.scandir(root / directory) as entries:
        for count, entry in enumerate(entries, 1):
            if count > vault_scope.METADATA_ENTRY_LIMIT:
                raise IntegrityError("layout_metadata_inventory_limit")
            relative = entry.name if directory == "." else directory + "/" + entry.name
            if entry.name.startswith(".") or not scope.reachable(relative):
                continue
            if entry.is_dir(follow_symlinks=False):
                observed.add((relative, True))
            elif scope.allows(relative) and (
                relative in omissions or entry.is_file(follow_symlinks=False)
            ):
                observed.add((relative, False))
    return observed


def candidates(session: Any, root: Path, scope: Any, omissions: Any) -> tuple[list[str], Any]:
    """A successful census supplies names, never authority to replace journal proof."""
    original: dict[str, Any] = {}

    @cloud_observation.retry_read
    def read() -> tuple[list[str], Any]:
        observed: dict[str, Any] = {}
        try:
            paths, directories = _candidates(session, root, scope, omissions, observed)
        except cloud_observation.ObservationError as error:
            if "paths" not in observed:
                # Without a complete prior name set, preflight cannot prove
                # that a fresh scan did not adopt an arriving visible file.
                # verify's outer retry retains its original journal proof.
                raise _IncompleteCensus(error) from error
            if not original:
                original.update(observed)
            # retry_read invalidates the active read session. An explicitly
            # supplied planning session can be a different instance.
            session.namespace = None
            session.children.clear()
            session.parsed.clear()
            session.bytes = 0
            raise
        if original:
            if "paths" in original and (
                sorted(paths) != original["paths"]
                or set(directories) != set(original["directories"])
            ):
                raise cloud_observation.ObservationError(
                    CODE, None, "link_paths_changed", changed=["paths"]
                )
            for path, before in original["directories"].items():
                if path not in directories:
                    raise cloud_observation.ObservationError(
                        CODE, root / path, "link_paths_changed", changed=["paths"]
                    )
                after = directories[path]
                if before[:3] != after[:3] or before[7:] != after[7:]:
                    raise cloud_observation.directory_changed(root / path, before, after, code=CODE)
            # Later items share the original inventory. Keep only this exact
            # refreshed proof, discarded with namespace on any invalidation.
            session.namespace = (*session.namespace[:3], dict(directories))
        return paths, directories

    with cloud_observation.scope(root), cloud_observation.phase("archive_link_census"):
        try:
            return read()
        except (cloud_observation.ObservationError, _IncompleteCensus) as caught:
            error = caught.error if isinstance(caught, _IncompleteCensus) else caught
            # A directory iterator can detect the same race before our final
            # signatures. Preserve its path/fields and the archive's public code.
            if str(error) == "note directory changed":
                error.args = (CODE,)
            raise error from None


def _candidates(
    session: Any, root: Path, scope: Any, omissions: Any, observed: dict[str, Any]
) -> tuple[list[str], Any]:
    key = str(root), scope.wire(), sorted(omissions)
    if session.namespace is not None and session.namespace[0] == key:
        _, paths, directories, _ = session.namespace
        observed.update(paths=sorted(paths), directories=dict(directories))
        refreshed = {}
        valid = True
        for directory, before in directories.items():
            path = root / directory
            require_local(path)
            after = signature(path)
            if after == before:
                continue
            transition = cloud_observation.directory_changed(path, before, after, code=CODE)
            if not transition.detail["retryable_read"]:
                raise transition
            # Our exchanges/staging can change directory times. Recheck only
            # the changed directory, retaining the exact previous visible names.
            current_children = _children(root, scope, omissions, directory)
            session.counts["link_directory_scans"] = (
                session.counts.get("link_directory_scans", 0) + 1
            )
            current = signature(path)
            if current != after:
                raise cloud_observation.directory_changed(path, after, current, code=CODE)
            if current_children != session.children[directory]:
                valid = False
                break
            refreshed[directory] = after
        if valid:
            directories.update(refreshed)
            session.counts["link_namespace_reused"] = (
                session.counts.get("link_namespace_reused", 0) + 1
            )
            return paths, directories
    directories = {".": signature(root)}
    # Keep identities even when a nested directory iterator fails before the
    # complete name census. The caller still owns its original path/body proof.
    if "paths" not in observed:
        observed.update(directories=directories)
    paths = list(omissions)
    session.counts["link_namespace_scans"] = session.counts.get("link_namespace_scans", 0) + 1
    for child, info in vault_scope.metadata_entries(
        root, scope, file_filter=lambda p: vault_scope.relative_path(root, p) not in omissions
    ):
        relative = vault_scope.relative_path(root, child)
        if stat.S_ISDIR(info.st_mode):
            directories[relative] = stat_signature(info)
        elif stat.S_ISREG(info.st_mode) and scope.allows(relative):
            paths.append(relative)
    observed.update(paths=sorted(set(paths)), directories=dict(directories))
    for path, before in directories.items():
        after = signature(root / path)
        if after != before:
            raise cloud_observation.directory_changed(root / path, before, after, code=CODE)
    children: dict[str, set[tuple[str, bool]]] = {path: set() for path in directories}
    for path in paths:
        parent = path.rpartition("/")[0] or "."
        if parent in children:
            children[parent].add((path, False))
    for directory in directories:
        if directory != ".":
            parent = directory.rpartition("/")[0] or "."
            if parent in children:
                children[parent].add((directory, True))
    session.children = children
    session.namespace = key, sorted(set(paths)), directories, None
    return session.namespace[1], directories
