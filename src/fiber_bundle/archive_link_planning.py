"""Bounded, task-local reference reuse; current inventories supply every source revision."""

import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from . import publication_links
from .cloud_observation import retry_read
from .config import IntegrityError
from .storage_read_scope import audited


class Candidates(list[str]):
    """Keep the resolver's list contract while indexing its exact ambiguity choices."""

    def __init__(self, paths: list[str]):
        super().__init__(paths)
        self.paths = set(paths)
        self.stems: dict[str, list[str]] = {}
        self.names: dict[str, list[str]] = {}
        for path in paths:
            name = posixpath.basename(path)
            self.names.setdefault(name, []).append(path)
            if path.lower().endswith(".md"):
                self.stems.setdefault(path[:-3], []).append(path)
                self.names.setdefault(name[:-3], []).append(path)

    def __contains__(self, value: object) -> bool:
        return value in self.paths


@dataclass
class Parsed:
    sha256: str
    references: Any
    names: set[str]
    error_offset: int | None = None
    size: int = 0


class LinkPlanningSession:
    """No authorization, inventory, filesystem proof, or resolved targets are cached."""

    def __init__(self) -> None:
        self.parsed: dict[str, Parsed] = {}
        self.bytes = 0
        self.namespace: tuple[Any, list[str], dict[str, Any], dict[str, Any] | None] | None = None
        self.children: dict[str, set[tuple[str, bool]]] = {}
        self.counts = {"link_body_parses": 0, "link_body_reused": 0, "link_sources_selected": 0}

    def candidates(self, root: Any, scope: Any, omissions: Any) -> tuple[list[str], dict[str, Any]]:
        from .archive_link_census import candidates

        return candidates(self, root, scope, omissions)

    def sources(self, inventory: Any, moves: dict[str, str]) -> list[tuple[Any, Parsed]]:
        visible = {note.relative_path for note in inventory.notes}
        self.parsed = {path: value for path, value in self.parsed.items() if path in visible}
        self.bytes = sum(value.size for value in self.parsed.values())
        moved_names = {posixpath.basename(path).removesuffix(".md") for path in moves}
        selected = []
        for note in inventory.notes:
            saved = self.parsed.get(note.relative_path)
            if saved is not None and saved.sha256 == note.sha256:
                self.counts["link_body_reused"] += 1
            else:
                self.counts["link_body_parses"] += 1
                try:
                    refs = publication_links.references(note.text)
                    names = set()
                    for begin, end, wiki, _, _ in refs:
                        raw = note.text[begin:end]
                        path = (
                            raw.split("|", 1)[0].split("#", 1)[0] if wiki else raw.split("#", 1)[0]
                        )
                        names.add(
                            posixpath.basename(unquote(path).split("?", 1)[0]).removesuffix(".md")
                        )
                    saved = Parsed(note.sha256, refs, names)
                except IntegrityError as error:
                    saved = Parsed(note.sha256, [], set(), int(getattr(error, "offset", 0)))
                saved.size = max(
                    len(note.text.encode()),
                    len(saved.references) * 256
                    + sum(len(name.encode()) * 4 + 128 for name in saved.names),
                )
                saved.size += len(note.relative_path.encode()) * 4 + 256
                previous = self.parsed.pop(note.relative_path, None)
                if previous is not None:
                    self.bytes -= previous.size
                # Parsed offsets and names are bounded independently of retained bodies.
                size = saved.size
                if len(self.parsed) < 10000 and self.bytes + size <= 32 * 1024 * 1024:
                    self.parsed[note.relative_path] = saved
                    self.bytes += size
            if (
                note.relative_path in moves
                or saved.names & moved_names
                or (
                    saved.error_offset is not None
                    and publication_links.may_reference_moved(note.text, note.relative_path, moves)
                )
            ):
                selected.append((note, saved))
        self.counts["link_sources_selected"] += len(selected)
        return selected


def observation(
    root: Any, inventory: Any, candidates: list[str], *, rechecked: Any = None
) -> dict[str, Any]:
    """Freeze current source revisions and names; callers persist this with their journal."""
    from .note_read_session import signature

    notes = {}
    for note in inventory.notes:
        current = signature(root / note.relative_path)
        expected = note.read_signature
        if expected is None or tuple(current) != tuple(expected):
            raise IntegrityError("placement_note_changed")
        notes[note.relative_path] = list(current)
    directories = dict(getattr(inventory, "directory_observations", None) or {})
    for path, expected in directories.items():
        current = signature(root / path)
        if current != tuple(expected):
            # Only a just-completed bounded census may refresh directory times.
            # Its original names/identities and every source signature survived;
            # any further change, identity, permissions or flags still conflicts.
            if (
                not rechecked
                or rechecked.get(path) != current
                or current[:3] != tuple(expected[:3])
                or current[7:] != tuple(expected[7:])
            ):
                raise IntegrityError("placement_note_changed")
            directories[path] = current
    from .inputs import canonical
    from .objects import digest

    return {
        "notes": notes,
        "path_sha256": digest(canonical(list(candidates)).encode()),
        "directories": directories,
    }


def compact(proof: dict[str, Any], updates: Any, moves: Any) -> None:
    """Persist aggregate unchanged evidence, keeping exact baselines only for our writes."""
    from .inputs import canonical
    from .objects import digest

    notes = proof.pop("notes")
    paths = sorted(notes)
    changed = set(moves) | {update["path"] for update in updates}
    proof.update(
        note_paths=paths,
        note_signatures_sha256=digest(canonical([[path, notes[path]] for path in paths]).encode()),
        before_notes={path: notes[path] for path in paths if path in changed},
    )


def _proof_changed(root: Any, reason: str, fields: list[str]) -> IntegrityError:
    from . import cloud_observation

    with cloud_observation.scope(root), cloud_observation.phase("archive_link_verify"):
        # Compact existing journals retain aggregate proofs, not the historical
        # full path/signature list. Do not invent an exact changed file name.
        return cloud_observation.ObservationError(
            "placement_link_sources_changed", None, reason, changed=fields
        )


@audited
@retry_read
def verify(root: Any, scope: Any, value: Any, *, namespace_addition: Any = None) -> None:
    """New/edited inbound sources invalidate an unwritten move, including after exchanges."""

    from .note_read_session import signature
    from .objects import digest
    from .publication_file_io import read

    proof = value.get("link_observation")
    if proof is None:
        return  # Existing journals retain their original evidence/recovery contract.
    omissions = value.get("read_omissions", {})
    from .note_read_session import current as read_session

    active = read_session.get()
    planning = active.links if active is not None else LinkPlanningSession()
    candidates, _ = planning.candidates(root, scope, omissions)
    actual = sorted(candidates)
    moved = not (root / value["source_root"]).exists() and (root / value["target_root"]).exists()
    from .inputs import canonical

    inverse = {new: old for old, new in value["moves"].items()} if moved else {}
    observed_paths = sorted(inverse.get(path, path) for path in actual)
    if namespace_addition is not None:
        from .committed_namespace_addition import paths

        observed_paths = paths(root, scope, value, namespace_addition, observed_paths)
    if "path_sha256" in proof:
        same_paths = digest(canonical(observed_paths).encode()) == proof["path_sha256"]
    else:
        same_paths = observed_paths == sorted(proof["paths"])
    if not same_paths:
        raise _proof_changed(
            root, "link_paths_changed", ["path_sha256" if "path_sha256" in proof else "paths"]
        )
    for path, expected in proof["directories"].items():
        actual_directory = (
            value["target_root"] + path[len(value["source_root"]) :]
            if moved
            and (path == value["source_root"] or path.startswith(value["source_root"] + "/"))
            else path
        )
        current = signature(root / actual_directory)
        # Our stage files and exchanges change times. Directory identity and
        # permissions remain mandatory; fresh candidate names above detect additions.
        if tuple(current[:3]) != tuple(expected[:3]):
            from . import cloud_observation

            with cloud_observation.scope(root), cloud_observation.phase("archive_link_verify"):
                raise cloud_observation.directory_changed(
                    root / actual_directory,
                    tuple(expected) + current[len(expected) :],
                    current,
                    code="placement_link_sources_changed",
                )
    updates = {u["path"]: u for u in value["updates"]}
    baselines = proof.get("before_notes", proof.get("notes", {}))
    normalized = []
    for path in proof.get("note_paths", sorted(baselines)):
        expected = baselines.get(path)
        actual_path = value["moves"].get(path, path) if moved else path
        update = updates.get(path)
        exchange = value["exchanges"].get(path)
        if update is not None and exchange is not None:
            raw, key = read(root, actual_path)
            if exchange["state"] == "exchanged":
                if digest(raw) != update["after_sha256"] or key[:2] != exchange["after_key"]:
                    raise IntegrityError("placement_link_sources_changed")
            elif digest(raw) == update["after_sha256"] and key[:2] == exchange["after_key"]:
                # A crash can follow the exchange before its outcome marker.
                # Both the exact displaced original and new inode must remain.
                backup, backup_key = read(root, exchange["stage"])
                if digest(backup) != update["before_sha256"] or backup_key != update["file_key"]:
                    raise IntegrityError("placement_link_sources_changed")
            elif digest(raw) != update["before_sha256"] or key != update["file_key"]:
                raise IntegrityError("placement_link_sources_changed")
        elif moved and path in value["moves"]:
            raw, key = read(root, actual_path)
            source = value["source_files"][path]
            if digest(raw) != source["sha256"] or key != source["file_key"]:
                raise IntegrityError("placement_link_sources_changed")
        else:
            current_signature = list(signature(root / actual_path))
            if "note_signatures_sha256" not in proof and current_signature != expected:
                raise IntegrityError("placement_link_sources_changed")
            normalized.append([path, current_signature])
            continue
        normalized.append([path, expected])
    if (
        "note_signatures_sha256" in proof
        and digest(canonical(normalized).encode()) != proof["note_signatures_sha256"]
    ):
        raise _proof_changed(root, "link_note_signatures_changed", ["note_signatures_sha256"])
