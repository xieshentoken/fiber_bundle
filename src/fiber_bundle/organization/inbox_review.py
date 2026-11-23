"""Current bounded Inbox diagnostics; historical tasks are evidence, never authorization."""

import os
import re
import stat
from typing import Any
from urllib.parse import urlsplit

from .. import (
    auth,
    file_exchange,
    publication_inventory_read,
    publication_links,
    vault_directories,
    vault_save_policy,
    vault_scope,
    vaults,
)
from ..config import IntegrityError, InvalidInput, StorageError, contained
from ..objects import digest
from . import jobs, packages, text_features

MAX_BYTES = 1024 * 1024
DISPLAY_BYTES = 65536


def directory(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]


def context(config: Any, db: Any, client: str, vault: str, path: str) -> tuple[Any, Any, Any]:
    root, allowed = packages.context(config, db, client, vault, write=True)
    row = db.execute("SELECT read_only FROM vaults WHERE id=?", (vault,)).fetchone()
    policy = vault_save_policy.get(config, db, vault)
    settings = policy["settings"]
    # Reject before probing any untrusted filename, including old task paths.
    if (
        not allowed.allows(path)
        or not vault_scope.inside(path, settings["inbox_dir"])
        or path == settings["inbox_dir"]
        or not path.lower().endswith(".md")
        or any(p.startswith(".") for p in path.split("/"))
        or vault_scope.inside(path, settings.get("moc_dir", "Map"))
        or vault_scope.inside(path, settings.get("book_dir", "Book"))
        or text_features.excluded(path, settings)
    ):
        raise auth.Denied(404)
    contained(root, path)
    vaults.require_scope(config, auth.grant(db, client, vault, "add"), path)
    if row[0]:
        raise InvalidInput("inbox_review_read_only")
    parent = path.rpartition("/")[0]
    parent_scope = vault_scope.effective(root / parent, {"include": [""], "exclude": []})
    if not parent_scope.allows(path.rsplit("/", 1)[-1]):
        raise auth.Denied(404)
    with vault_directories.opened_parent(root, allowed, parent) as (_, fd):
        parent_key = directory(os.fstat(fd))
    # Package members are not ordinary notes, even if their old task said otherwise.
    for ancestor in (root / path).parents:
        if ancestor == root:
            break
        if (ancestor / "manifest.json").exists() or (ancestor / ".fiber-expert-canonical").exists():
            raise InvalidInput("inbox_review_package_not_supported")
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    return (
        root,
        allowed,
        {
            "root": directory(root.stat()),
            "parent": parent_key,
            "scope": allowed.wire(),
            "parent_scope": parent_scope.wire(),
            "policy_revision": policy["revision"],
            "permission_epoch": epoch[0] if epoch else 0,
            "settings": settings,
        },
    )


def read(root: Any, path: str) -> tuple[bytes, Any]:
    """Pin the parent and descriptor; content and identity must describe the same read."""
    from .. import cloud_files

    cloud_files.require_available(contained(root, path))
    fd = file_exchange.parent_fd(root, path)
    try:
        name = path.rsplit("/", 1)[-1]
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
        raw, key = file_exchange.read(fd, name)
        after = os.stat(name, dir_fd=fd, follow_symlinks=False)

        def signature(s: os.stat_result) -> Any:
            return (
                s.st_dev,
                s.st_ino,
                s.st_size,
                s.st_mtime_ns,
                s.st_ctime_ns,
                s.st_mode,
                s.st_uid,
                s.st_gid,
                s.st_nlink,
            )

        if signature(before) != signature(after) or list(key) != [before.st_dev, before.st_ino]:
            raise IntegrityError("inbox_review_file_changed")
        birth = getattr(before, "st_birthtime", 0)
        return raw, {
            "sha256": digest(raw),
            "key": [*key, float(birth).hex()],
            "mode": stat.S_IMODE(before.st_mode),
            "uid": before.st_uid,
            "gid": before.st_gid,
        }
    finally:
        os.close(fd)


def classification(raw: bytes) -> str:
    text = raw.decode("utf-8-sig")
    stripped = text.strip()
    if not stripped:
        return "empty"
    # Deliberately narrow: inline syntax, HTML, code and links all count as content.
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if (
        len(lines) == 1
        and re.fullmatch(r"#{1,6} [^\r\n\[\]!*_`<>\\#]+", lines[0])
        and not re.search(r"\w+://|www\.", lines[0], re.I)
    ):
        return "title_only"
    return "content"


def publications(db: Any, vault: str, path: str) -> list[Any]:
    return [
        {"kind": row["kind"], "publication_id": row["publication_id"], "state": row["state"]}
        for row, plan in publication_inventory_read.rows(db, vault)
        if path in plan["mapping"].values() or path == row["package_root"]
    ]


def reported(job: Any, path: str) -> set[tuple[int, str]]:
    return {
        (link.get("line", 0), link.get("target", ""))
        for item in job.get("items", [])
        if item.get("path") == path
        for link in item.get("archive_preflight", {}).get("links", [])
        if link.get("note_path") == path
    }


def task(config: Any, db: Any, client: str, vault: str, job_id: str, path: str) -> Any:
    job = jobs.get(config, db, client, vault, job_id)
    paths = {item.get("path") for item in job.get("items", [])}
    paths.update(item.get("path") for item in job.get("candidate_coverage", {}).get("skipped", []))
    if path not in paths:
        raise auth.Denied(404)
    return job


def repairable(root: Any, allowed: Any, job: Any, path: str, raw: bytes) -> list[Any]:
    """Only explicitly reported, currently missing ordinary references are offered."""
    from ..archive_link_planning import LinkPlanningSession
    from ..note_transfer_links import resolve

    wanted = reported(job, path)
    if not wanted:
        return []
    candidates = None
    text = raw.decode("utf-8-sig")
    result = []
    for begin, end, wiki, start, stop in publication_links.references(text):
        fragment = text[start:stop]
        target = text[begin:end].split("|", 1)[0].split("#", 1)[0]
        line = text.count("\n", 0, start) + 1
        if (
            (line, target) not in wanted
            or fragment.startswith("!")
            or not target
            or urlsplit(target).scheme
            or target.startswith("//")
            or "?" in target
            or "\\" in target
            or not (fragment.startswith("[[") if wiki else re.match(r"^\[[^\]\n]*\]\(", fragment))
        ):
            continue
        try:
            if wiki:
                # An unqualified wiki target requires the complete visible namespace.
                if allowed.include != ("",) or allowed.exclude:
                    continue
                if candidates is None:
                    candidates, _ = LinkPlanningSession().candidates(root, allowed, {})
                resolved = resolve(target, root, path, candidates, wiki=True)
                if resolved in candidates:
                    continue
            else:
                resolved = resolve(target, root, path, [], wiki=False)
                if not allowed.allows(resolved):
                    continue
                parent, name = resolved.rpartition("/")[0], resolved.rsplit("/", 1)[-1]
                # Relative inline links use an exact target, not basename resolution.
                # Only ENOENT for that direct child proves missing; unknown parents,
                # permissions, cloud state, directories and links remain untouched.
                with vault_directories.opened_parent(root, allowed, parent) as (parent_path, fd):
                    local = vault_scope.effective(parent_path, {"include": [""], "exclude": []})
                    if not local.allows(name):
                        continue
                    try:
                        os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        continue
        except (StorageError, OSError):
            continue  # An ambiguous/unsupported link is never treated as missing.
        replacement = (
            text[begin:end].split("|", 1)[-1]
            if wiki
            else fragment.replace("[", "\\[", 1).replace("]", "\\]", 1)
        )
        line_start, line_end = text.rfind("\n", 0, start) + 1, text.find("\n", stop)
        if line_end < 0:
            line_end = len(text)
        before = text[line_start:line_end].rstrip("\r")
        after = (text[line_start:start] + replacement + text[stop:line_end]).rstrip("\r")
        result.append(
            {
                "id": digest(f"{digest(raw)}:{start}:{stop}:{fragment}".encode()),
                "line": line,
                "target": target,
                "before": before,
                "after": after,
                "kind": "wiki" if wiki else "markdown",
                "start": start,
                "stop": stop,
                "replacement": replacement,
            }
        )
    return result


def inspect(config: Any, db: Any, client: str, vault: str, job_id: str, path: str) -> Any:
    root, allowed, _ = context(config, db, client, vault, path)
    job = task(config, db, client, vault, job_id, path)
    state, raw = "unreadable", b""
    links: list[Any] = []
    issues: list[Any] = []
    evidence: dict[str, Any] = {}
    try:
        raw, evidence = read(root, path)
        state = classification(raw)
        links = repairable(root, allowed, job, path, raw)
    except (OSError, StorageError, UnicodeError) as error:
        issues.append(
            {"code": "inbox_review_unreadable", "reason": str(error), "next_step": "reinspect"}
        )
    from .inbox_review_plan import history

    visible = raw[:DISPLAY_BYTES].decode("utf-8-sig", errors="replace")
    return {
        "path": path,
        "classification": state,
        "byte_count": len(raw) if state != "unreadable" else None,
        "sha256": evidence.get("sha256"),
        "text": {
            "lines": [
                {"number": i, "text": line} for i, line in enumerate(visible.splitlines(), 1)
            ],
            "truncated": len(raw) > DISPLAY_BYTES,
            "max_bytes": DISPLAY_BYTES,
        },
        "publication": publications(db, vault, path),
        "issues": issues,
        "actions": {
            "quarantine_empty": state in {"empty", "title_only"},
            "unlink_missing": bool(links),
        },
        "links": [
            {k: v for k, v in link.items() if k not in {"start", "stop", "replacement"}}
            for link in links
        ],
        "history": [
            o for o in history(config, db, client, vault, path)["operations"] if o["path"] == path
        ],
        "index_state": "repreview_required",
        "model_called": False,
    }
