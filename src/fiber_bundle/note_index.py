"""Bounded, ephemeral read-only Markdown snapshots. No credentials, DB or network."""

import hashlib
import json
import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import note_publications
from .config import IntegrityError, contained
from .vault_scope import Scope


@dataclass(frozen=True)
class Limits:
    note_bytes: int = 1024 * 1024
    total_bytes: int = 32 * 1024 * 1024
    entries: int = 10000
    depth: int = 32


@dataclass(frozen=True)
class Note:
    relative_path: str
    sha256: str
    title: str
    text: str
    publication_id: str | None = None
    file_key: tuple[int, int, str] | None = None
    identity: str | None = None
    publication_path: str | None = None


def read_note(vault: Path, relative: str, limit: int) -> Note:
    path = contained(vault, relative)
    from .cloud_files import require_available

    require_available(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise IntegrityError("note is not a bounded independent regular file")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(data) != info.st_size or (info.st_size, info.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise IntegrityError("note changed while reading")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IntegrityError("note encoding unsupported") from exc
    title = path.stem
    for line in text.splitlines()[:40]:
        if line.startswith("# "):
            title = line[2:].strip() or title
            break
        if line.startswith("title: "):
            value = line[7:].strip()
            try:
                parsed = json.loads(value)
                if isinstance(parsed, str):
                    title = parsed
            except ValueError:
                title = value.strip("\"'") or title
            break
    birth = getattr(info, "st_birthtime", None)
    key = (info.st_dev, info.st_ino, float(birth).hex()) if birth is not None else None
    return Note(relative, hashlib.sha256(data).hexdigest(), title[:200], text, file_key=key)


def snapshot(
    vault: Path,
    published: set[str],
    reserved: set[str],
    limits: Limits | None = None,
    *,
    publications: dict[str, note_publications.Binding] | None = None,
    tracked_publications: dict[str, tuple[int, int, str]] | None = None,
    located_paths: dict[str, str] | None = None,
    plain_published: set[str] | None = None,
    on_note: Callable[[Note], None] | None = None,
    note_loader: Callable[[str, int], Note] | None = None,
    scope: Scope | None = None,
    managed_entries: dict[str, dict[str, Any]] | None = None,
    hidden_files: set[str] | None = None,
    hidden_roots: set[str] | None = None,
    exclude_path: Callable[[str], bool] | None = None,
) -> list[Note]:
    """Managed bundles contribute only their published note; hidden/partial bundles stay out."""
    limits = limits or Limits()
    scope = scope or Scope()
    notes: list[Note] = []
    entries = 0
    total = 0
    located: set[str] = set()
    unpublished = reserved - published

    def retain(note: Note) -> None:
        if on_note is None:
            notes.append(note)
        else:
            on_note(note)
            # A streaming consumer keeps metadata for identity resolution, not library bodies.
            notes.append(replace(note, text=""))

    def consume(size: int) -> None:
        nonlocal total
        total += size
        if total > limits.total_bytes:
            raise IntegrityError("vault index byte limit exceeded")

    reader = note_loader or (lambda relative, maximum: read_note(vault, relative, maximum))

    def add(relative: str, publication_id: str | None = None) -> None:
        if not scope.allows(relative) or relative in (hidden_files or set()):
            return
        note = reader(relative, limits.note_bytes)
        managed = (managed_entries or {}).get(relative)
        if managed:
            from .publication_locations import annotate

            note = annotate(note, managed)
            publication_id = note.publication_id
            if publication_id and located_paths is not None:
                located_paths[publication_id] = (
                    managed["package_root"] or relative.rsplit("/", 1)[0]
                )
        consume(contained(vault, relative).stat().st_size)
        retain(
            replace(
                note,
                publication_id=publication_id,
                publication_path=relative.rsplit("/", 1)[0] if publication_id else None,
            )
        )

    def tracked_note(directory: Path, publication_id: str, key: tuple[int, int, str]) -> None:
        nonlocal entries
        matches: list[Note] = []
        markdown = 0
        with os.scandir(directory) as items:
            for entry in items:
                relative = Path(entry.path).relative_to(vault).as_posix()
                if not scope.reachable(relative) or (exclude_path and exclude_path(relative)):
                    continue
                entries += 1
                if entries > limits.entries:
                    raise IntegrityError("vault index entry limit exceeded")
                path = Path(entry.path)
                if (
                    path.name.startswith(".")
                    or not entry.is_file(follow_symlinks=False)
                    or path.suffix.lower() != ".md"
                ):
                    continue
                relative = path.relative_to(vault).as_posix()
                if not scope.allows(relative):
                    continue
                note = reader(relative, limits.note_bytes)
                consume(contained(vault, relative).stat().st_size)
                markdown += 1
                if note.file_key == key:
                    matches.append(note)
        if len(matches) > 1 or (not matches and markdown):
            raise IntegrityError("tracked publication file identity unresolved")
        if matches:
            retain(
                replace(
                    matches[0],
                    publication_id=publication_id,
                    publication_path=directory.relative_to(vault).as_posix(),
                )
            )

    def walk(directory: Path, depth: int) -> None:
        nonlocal entries
        if depth > limits.depth:
            raise IntegrityError("vault index depth limit exceeded")
        # Count entries before sorting; huge directories must not allocate an unbounded list.
        paths = []
        with os.scandir(directory) as items:
            for entry in items:
                relative = Path(entry.path).relative_to(vault).as_posix()
                if not scope.reachable(relative) or (exclude_path and exclude_path(relative)):
                    continue
                entries += 1
                if entries > limits.entries:
                    raise IntegrityError("vault index entry limit exceeded")
                paths.append(Path(entry.path))
        for path in sorted(paths):
            relative = path.relative_to(vault).as_posix()
            if relative in (hidden_roots or set()):
                continue
            if path.name.startswith(".") or path.is_symlink():
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                from .expert_package_boundary import detected

                if detected(vault, relative, scope):
                    continue
                marker = path / note_publications.MARKER
                analysis_marker = path / ".fiber-analysis.json"
                if (analysis_marker.exists() or analysis_marker.is_symlink()) and relative not in (
                    plain_published or set()
                ):
                    continue
                if relative in unpublished:
                    continue
                if publications is not None and (marker.exists() or marker.is_symlink()):
                    data = note_publications.read_marker(vault, relative, limits.note_bytes)
                    consume(len(data))
                    bound = note_publications.identify(data, publications)
                    if bound is None:
                        if relative in published:
                            raise IntegrityError("published bundle identity missing")
                        continue
                    if bound.publication_id in located:
                        raise IntegrityError("publication identity occurs in multiple locations")
                    located.add(bound.publication_id)
                    if located_paths is not None:
                        located_paths[bound.publication_id] = relative
                    tracked = (tracked_publications or {}).get(bound.publication_id)
                    if tracked is not None:
                        tracked_note(path, bound.publication_id, tracked)
                        continue
                    note = contained(vault, relative + "/note.md")
                    if note.exists():
                        add(relative + "/note.md", bound.publication_id)
                elif relative in published:
                    if publications is not None and relative not in (plain_published or set()):
                        raise IntegrityError("published bundle marker missing")
                    note = contained(vault, relative + "/note.md")
                    if note.exists():
                        add(relative + "/note.md")
                elif relative not in reserved and not marker.exists():
                    walk(path, depth + 1)
            elif stat.S_ISREG(mode) and path.suffix.lower() == ".md":
                add(relative)

    walk(vault, 0)
    visible = {n.relative_path for n in notes}
    if any(
        scope.allows(path) and not (exclude_path and exclude_path(path)) and path not in visible
        for path in (managed_entries or {})
    ):
        raise IntegrityError("layout_rebind_required")
    return notes


def passages(text: str) -> Iterator[tuple[int, str]]:
    """Line references address original text; windows preserve bytes as decoded, not summaries."""
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            for start in range(0, len(line), 320):
                yield number, line[start : start + 400]


def adjacent_passages(text: str) -> Iterator[tuple[int, str]]:
    """At most three physical lines and 400 characters; exact separators retained.

    Single-line passages remain the preferred compatibility path. No distant
    concatenation or truncated adjacent line can manufacture a source quote.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        for size in (2, 3):
            group = lines[index : index + size]
            if len(group) != size or any(not value.strip() for value in group):
                continue
            passage = "".join(group).rstrip("\r\n")
            if len(passage) <= 400:
                yield index + 1, passage
