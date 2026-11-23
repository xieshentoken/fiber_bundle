"""Read-only archive readiness, separate from reliable MOC membership changes."""

import json
import stat
from dataclasses import replace
from typing import Any

from .. import auth, note_index, note_placement, partial_notes, publication_file_io, vault_scope
from ..config import IntegrityError
from ..inputs import canonical
from ..note_read_session import stat_signature
from ..objects import digest
from . import packages


def inventory(config: Any, db: Any, job: Any, *, preview: bool = False) -> Any:
    grant: Any = auth.grant(db, job["client_id"], job["vault_id"], "query")
    extra = {}
    if preview:
        from . import text_features

        _, allowed = packages.context(config, db, job["client_id"], job["vault_id"])
        settings = job["fixed"]["save_policy"]["settings"]
        selected = []
        for desired in [*job["fixed"]["include"], settings["moc_dir"]]:
            for base in allowed.include:
                if vault_scope.inside(desired, base):
                    selected.append(desired)
                elif vault_scope.inside(base, desired):
                    selected.append(base)
        grant = {
            **dict(grant),
            "scope_json": json.dumps(
                {
                    "include": sorted(set(selected)),
                    "exclude": sorted(
                        set(allowed.exclude) | set(job["fixed"]["policy"]["settings"]["exclude"])
                    ),
                }
            ),
        }
        extra["exclude_path"] = lambda path: text_features.excluded(path, settings)
    return partial_notes.read(config, db, grant, partial_notes.for_job(job), **extra)


def inspect(
    config: Any, db: Any, job: Any, item: Any, *, notes: Any = None, session: Any = None
) -> Any:
    target = item.get("target")
    if not target:
        return {"state": "deferred" if item.get("deferred_target") else "unplanned"}
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    source = item["path"]
    if not allowed.allows(source) or not allowed.allows(target):
        raise IntegrityError("maintenance_archive_scope_changed")
    if (root / target).exists() or (root / target).is_symlink():
        return {"state": "blocked", "reason": "maintenance_archive_target_conflict", "links": []}
    if item["kind"] == "expert":
        paths = [source + "/" + name for name in publication_file_io.tree(root, source, allowed)]
    else:
        paths = [source]
    moves = {path: target + path[len(source) :] for path in paths}
    notes = notes if notes is not None else inventory(config, db, job)
    updates, missing = note_placement.archive_preflight(
        root, allowed, notes, moves, session=session
    )
    links = [dict(link) for link in missing]
    # Only inputs that affect this move bind its readiness. Unrelated edits do
    # not change the contract; new/changed inbound links do.
    evidence = [
        [u["path"], u["target"], u["before_sha256"], u["file_key"], u["after_sha256"]]
        for u in updates
    ]
    return {
        "state": "blocked" if links else "ready",
        "reason": "maintenance_archive_link_review_required" if links else None,
        "links": links,
        "rewritten_paths": [u["path"] for u in updates],
        "binding": digest(canonical([moves, evidence, links]).encode()),
    }


def prepare(config: Any, db: Any, job: Any, *, validate: bool = False) -> None:
    """Freeze readiness before sealing; confirmation only compares it, never rebases."""
    from .manual_resolution import resolved

    handled = resolved(config, job)
    candidates = [i for i in job["items"] if i.get("target") and i["entity"] not in handled]
    if not candidates:
        return
    from ..archive_link_planning import LinkPlanningSession
    from ..note_read_session import current as read_session

    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    source_signatures = metadata_sources(root, allowed)
    notes = inventory(config, db, job, preview=True)
    if validate:
        notes = changed_sources(root, allowed, job, notes, source_signatures)
    active = read_session.get()
    session = active.links if active is not None else LinkPlanningSession()
    observations = {
        i["entity"]: {
            **inspect(config, db, job, i, notes=notes, session=session),
            "coverage": "selected_scope",
        }
        for i in candidates
    }
    while True:
        blocked_paths = {
            i["path"] for i in candidates if observations[i["entity"]]["state"] == "blocked"
        }
        changed = False
        for item in candidates:
            before = observations[item["entity"]]
            current = protect_sources(before, blocked_paths - {item["path"]})
            changed |= current != before
            observations[item["entity"]] = current
        if not changed:
            break
    if metadata_sources(root, allowed) != source_signatures:
        from .catalog_inventory import Unavailable

        raise Unavailable("maintenance_archive_dependency_unavailable", {"": "changing_sources"})
    if not validate:
        job["archive_scope"] = {"version": 1, "sources": source_signatures}
    for item in candidates:
        current = observations[item["entity"]]
        if validate:
            previous = item.get("archive_preflight", {})
            if previous != current:
                from .catalog_inventory import Unavailable

                paths = set(current.get("rewritten_paths", [])) - set(
                    previous.get("rewritten_paths", [])
                )
                paths.update(
                    link["note_path"]
                    for link in current.get("links", [])
                    if link not in previous.get("links", [])
                )
                raise Unavailable(
                    "maintenance_preview_changed",
                    {path: "backlink_changed" for path in sorted(paths)}
                    or {item["path"]: "archive_inputs_changed"},
                )
        else:
            item["archive_preflight"] = current


def defer(config: Any, db: Any, job: Any, item: Any, *, notes: Any = None) -> bool:
    """Park only a newly checked, unwritten archive. Never bypass an existing intent."""
    if item.get("archive_operation") or item.get("intent"):
        return bool(item.get("archive_deferral"))
    current = inspect(config, db, job, item, notes=notes)
    protected = {
        i["path"]
        for i in job["items"]
        if i is not item
        and i["state"] != "done"
        and (i.get("archive_wait") or i.get("archive_preflight", {}).get("state") == "blocked")
    }
    current = protect_sources(current, protected)
    item["archive_preflight"] = current
    if current["state"] != "blocked" or current["reason"] not in {
        "maintenance_archive_link_review_required",
        "maintenance_archive_waiting_dependency",
    }:
        item.pop("archive_wait", None)
        return False
    item["archive_wait"] = current
    return True


def protect_sources(proof: Any, paths: set[str]) -> Any:
    """A deferred source is not an editable link dependency of a later move."""
    affected = sorted(
        source
        for source in paths
        if any(
            path == source or path.startswith(source + "/")
            for path in proof.get("rewritten_paths", [])
        )
    )
    if proof["state"] != "ready" or not affected:
        return proof
    return {
        **proof,
        "state": "blocked",
        "reason": "maintenance_archive_waiting_dependency",
        "blocked_sources": affected,
    }


def metadata_sources(root: Any, allowed: Any) -> dict[str, Any]:
    """Names and revisions detect possible new backlinks without reading their bodies."""
    return {
        vault_scope.relative_path(root, path): list(stat_signature(info))
        for path, info in vault_scope.metadata_entries(
            root, allowed, file_filter=lambda path: path.suffix.lower() == ".md"
        )
        if stat.S_ISREG(info.st_mode) and path.suffix.lower() == ".md"
    }


def changed_sources(root: Any, allowed: Any, job: Any, notes: Any, signatures: Any) -> Any:
    """At confirmation read only changed external Markdown, with explicit bounds."""
    from .catalog_inventory import Unavailable

    before = job.get("archive_scope", {}).get("sources")
    if not isinstance(before, dict):
        raise IntegrityError("maintenance_preview_changed")
    visible = {n.relative_path for n in notes.notes}
    changed = sorted(
        p
        for p in set(before) | set(signatures)
        if p not in visible and before.get(p) != signatures.get(p)
    )
    missing = {p: "source_missing" for p in changed if p not in signatures}
    if missing or len(changed) > 32:
        raise Unavailable(
            "maintenance_archive_dependency_unavailable",
            missing or {p: "source_budget" for p in changed},
        )
    added = []
    size = 0
    for path in changed:
        if not allowed.allows(path):
            raise IntegrityError("maintenance_archive_scope_changed")
        limit = min(1024 * 1024, 4 * 1024 * 1024 - size)
        if limit <= 0 or signatures[path][4] > limit:
            raise Unavailable("maintenance_archive_dependency_unavailable", {path: "source_budget"})
        try:
            note = note_index.read_note(root, path, limit)
        except (OSError, IntegrityError) as error:
            raise Unavailable(
                "maintenance_archive_dependency_unavailable", {path: "source_unavailable"}
            ) from error
        size += int(note.read_signature[4]) if note.read_signature else len(note.text.encode())
        added.append(note)
    return replace(notes, notes=[*notes.notes, *added])
