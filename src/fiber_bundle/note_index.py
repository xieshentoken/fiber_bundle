"""Bounded, ephemeral read-only Markdown snapshots. No credentials, DB or network."""

import hashlib
import json
import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from . import note_publications
from .config import IntegrityError, StorageError, contained
from .vault_scope import Scope, relative_path


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
    read_signature: tuple[int | float, ...] | None = None


def metadata(note: Note) -> dict[str, Any]:
    """Keep ephemeral read proof out of the existing persistent-index wire format."""
    value = asdict(note)
    value.pop("read_signature")
    return value


def read_note(
    vault: Path,
    relative: str,
    limit: int,
    *,
    availability: Callable[[Path], None] | None = None,
) -> Note:
    path = contained(vault, relative)
    from .cloud_files import require_available

    (availability or require_available)(path)
    from .note_read_session import current

    session = current.get()
    if session is not None:
        return session.note(path, limit, lambda: _read_note(path, relative, limit))
    return _read_note(path, relative, limit)


def _read_note(path: Path, relative: str, limit: int) -> Note:
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
    from .note_read_session import stat_signature

    return Note(
        relative,
        hashlib.sha256(data).hexdigest(),
        title[:200],
        text,
        file_key=key,
        read_signature=stat_signature(after),
    )


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
    known_entries: dict[str, tuple[int, int]] | None = None,
    note_errors: dict[str, str] | None = None,
    directory_observations: dict[str, tuple[int | float, ...]] | None = None,
    on_skip: Callable[[str, str], None] | None = None,
    recheck_directory_times: bool = False,
) -> list[Note]:
    """Managed bundles contribute only their published note; hidden/partial bundles stay out."""
    limits = limits or Limits()
    scope = scope or Scope()
    notes: list[Note] = []
    entries = 0
    total = 0
    located: set[str] = set()
    unpublished = reserved - published

    def skipped(relative: str, reason: str) -> None:
        if on_skip is not None and scope.allows(relative):
            on_skip(relative, reason)

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

    from . import display_observation
    from .cloud_read_batch import CloudReadBatch

    cloud_batch = (
        CloudReadBatch(vault)
        if note_loader is None
        and "Mobile Documents" in vault.parts
        and display_observation.current.get() is None
        else None
    )

    def reader(relative: str, maximum: int) -> Note:
        if note_loader is not None:
            return note_loader(relative, maximum)
        if cloud_batch is not None:
            return read_note(vault, relative, maximum, availability=cloud_batch.check)
        return read_note(vault, relative, maximum)

    def add_checked(relative: str, publication_id: str | None = None) -> None:
        if not scope.allows(relative):
            return
        if relative in (hidden_files or set()):
            skipped(relative, "managed_publication_not_visible")
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
        consume(
            known_entries[relative][1]
            if known_entries is not None
            else contained(vault, relative).stat().st_size
        )
        retain(
            replace(
                note,
                publication_id=publication_id,
                publication_path=relative.rsplit("/", 1)[0] if publication_id else None,
            )
        )

    def add(relative: str, publication_id: str | None = None) -> None:
        if note_errors is not None and relative in note_errors:
            skipped(relative, note_errors[relative])
            return
        try:
            add_checked(relative, publication_id)
        except (StorageError, OSError) as error:
            if note_errors is None:
                raise
            from .vault_sync.problems import reason

            note_errors[relative] = reason(error)
            skipped(relative, note_errors[relative])

    def tracked_note(directory: Path, publication_id: str, key: tuple[int, int, str]) -> None:
        nonlocal entries
        matches: list[Note] = []
        markdown = 0
        with os.scandir(directory) as items:
            for entry in items:
                relative = relative_path(vault, Path(entry.path))
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
                relative = relative_path(vault, path)
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
                    publication_path=relative_path(vault, directory),
                )
            )

    def children(directory: Path) -> Iterator[Path]:
        from .note_read_session import names

        for name in names(directory, limits.entries):
            path = directory / name
            relative = relative_path(vault, path)
            if scope.reachable(relative) and not (exclude_path and exclude_path(relative)):
                yield path

    def relevant_child(path: Path) -> bool:
        return not path.name.startswith(".") or path.name in {
            note_publications.MARKER,
            ".fiber-analysis.json",
        }

    def recheck_children(
        directory: Path,
        proof: dict[Path, tuple[int | float, ...]],
        before: tuple[int | float, ...],
    ) -> None:
        from .cloud_observation import ObservationError, directory_changed
        from .note_read_session import current, signature

        seen = set()
        for path in children(directory):
            if not relevant_child(path):
                continue
            expected = proof.get(path)
            if expected is None:
                raise ObservationError(
                    "note directory entries changed",
                    path,
                    "directory_entries_changed",
                    changed=["added"],
                )
            observed = signature(path)
            if observed != expected:
                changed = directory_changed(path, expected, observed)
                if stat.S_ISDIR(int(expected[2])) and changed.detail["retryable_read"]:
                    raise changed
                raise ObservationError(
                    "note directory entries changed",
                    path,
                    "directory_entries_changed",
                    changed=changed.detail["changed_fields"],
                )
            seen.add(path)
        if missing := proof.keys() - seen:
            raise ObservationError(
                "note directory entries changed",
                min(missing),
                "directory_entries_changed",
                changed=["removed"],
            )
        after = signature(directory)
        if before != after:
            raise directory_changed(directory, before, after)
        if (session := current.get()) is not None:
            session.counts["finalize_directory_rechecks"] = (
                session.counts.get("finalize_directory_rechecks", 0) + 1
            )

    def walk(directory: Path, depth: int) -> tuple[int | float, ...]:
        nonlocal entries
        from .note_read_session import signature

        before_directory = signature(directory)
        if depth > limits.depth:
            raise IntegrityError("vault index depth limit exceeded")
        # Count entries before sorting; huge directories must not allocate an unbounded list.
        paths = []
        if known_entries is not None:
            parent = relative_path(vault, directory)
            parent = "" if parent == "." else parent
            paths = [
                vault / relative
                for relative in known_entries
                if relative
                and (relative.rsplit("/", 1)[0] if "/" in relative else "") == parent
                and scope.reachable(relative)
                and not (exclude_path and exclude_path(relative))
            ]
        else:
            for path in children(directory):
                entries += 1
                if entries > limits.entries:
                    raise IntegrityError("vault index entry limit exceeded")
                paths.append(path)
        # Archive finalization already verifies the original link/write proof.
        # Keep a short metadata census so a long body walk can tolerate directory
        # time drift only when every in-scope direct entry still matches.
        proof = (
            {path: signature(path) for path in paths if relevant_child(path)}
            if recheck_directory_times and known_entries is None
            else None
        )
        from .display_observation import current, preflight

        if current.get() is not None or cloud_batch is not None:
            check = cloud_batch.preflight if cloud_batch is not None else preflight
            eligible = [
                path
                for path in paths
                if not path.name.startswith(".")
                and path.suffix.lower() == ".md"
                and scope.allows(relative_path(vault, path))
                and relative_path(vault, path) not in (hidden_files or set())
                and relative_path(vault, path) not in (hidden_roots or set())
                and relative_path(vault, path) not in (note_errors or {})
                and not path.is_symlink()
                and path.is_file()
            ]
            try:
                check(directory, eligible)
            except (StorageError, OSError):
                if note_errors is None:
                    raise
                # A failed metadata batch does not identify which member failed.
                # Isolate it once per member; successful members retain batch proofs.
                from .vault_sync.problems import reason

                for path in eligible:
                    try:
                        check(directory, [path])
                    except (StorageError, OSError) as error:
                        note_errors[relative_path(vault, path)] = reason(error)
        for path in sorted(paths):
            relative = relative_path(vault, path)
            if relative in (hidden_roots or set()):
                skipped(relative, "managed_content_hidden")
                continue
            if proof is not None and not relevant_child(path) and path.suffix.lower() != ".md":
                continue
            if known_entries is not None:
                mode = known_entries[relative][0]
            elif proof is not None and path in proof:
                mode = int(proof[path][2])
            else:
                mode = path.lstat().st_mode
            if path.name.startswith(".") or stat.S_ISLNK(mode):
                if path.suffix.lower() == ".md":
                    skipped(relative, "symbolic_link" if stat.S_ISLNK(mode) else "hidden_file")
                continue
            if stat.S_ISDIR(mode):
                from .expert_package_boundary import detected

                if detected(vault, relative, scope):
                    skipped(relative, "expert_package_not_registered")
                    continue
                marker = path / note_publications.MARKER
                analysis_marker = path / ".fiber-analysis.json"
                if (analysis_marker.exists() or analysis_marker.is_symlink()) and relative not in (
                    plain_published or set()
                ):
                    skipped(relative, "publication_not_committed")
                    continue
                if relative in unpublished:
                    skipped(relative, "publication_not_committed")
                    continue
                if publications is not None and (marker.exists() or marker.is_symlink()):
                    data = note_publications.read_marker(vault, relative, limits.note_bytes)
                    consume(len(data))
                    bound = note_publications.identify(data, publications)
                    if bound is None:
                        if relative in published:
                            raise IntegrityError("published bundle identity missing")
                        skipped(relative, "publication_not_committed")
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
                    checked = walk(path, depth + 1)
                    if proof is not None and checked != proof[path]:
                        from .cloud_observation import directory_changed

                        change = directory_changed(path, proof[path], checked)
                        if not change.detail["retryable_read"]:
                            raise change
                        if (
                            marker.exists()
                            or marker.is_symlink()
                            or analysis_marker.exists()
                            or analysis_marker.is_symlink()
                            or detected(vault, relative, scope)
                        ):
                            raise IntegrityError("note directory boundary changed")
                        # The child just verified its own census. Keep that
                        # observation rather than comparing it to the pre-walk time.
                        proof[path] = checked
            elif stat.S_ISREG(mode) and path.suffix.lower() == ".md":
                add(relative)
        after_directory = signature(directory)
        if after_directory != before_directory:
            from .cloud_observation import directory_changed

            error = directory_changed(directory, before_directory, after_directory)
            if proof is None or not set(error.detail["changed_fields"]) <= {"mtime_ns", "ctime_ns"}:
                raise error
            recheck_children(directory, proof, after_directory)
        if directory_observations is not None:
            directory_observations[relative_path(vault, directory)] = after_directory
        return after_directory

    from .cloud_observation import scope as diagnostic_scope

    with diagnostic_scope(vault):
        walk(vault, 0)
    if cloud_batch is not None:
        cloud_batch.verify()
    visible = {n.relative_path for n in notes}
    if any(
        scope.allows(path)
        and not (exclude_path and exclude_path(path))
        and path not in visible
        and path not in (note_errors or {})
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
