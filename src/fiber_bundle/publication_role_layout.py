"""Version-two placement of body files and shared attachments, without filesystem writes."""

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from . import publication_note_roles
from .objects import digest

IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"}


def mapping(
    settings: dict[str, Any],
    kind: str,
    identity: str,
    heading: str,
    files: dict[str, bytes],
    claimed: set[str],
    choose: Callable[[str, str, str, str, set[str]], str],
    *,
    expert_inbox: bool = False,
) -> tuple[str, str | None, dict[str, str], set[str], dict[str, str]]:
    body = {"note.md"}
    supplements: dict[str, str] = {}
    if kind == "expert":
        body.update(
            n
            for n in files
            if n.endswith(".md") and not any(part.startswith(".") for part in n.split("/"))
        )
    if kind != "expert":
        body, supplements = publication_note_roles.roles(files)
    package = (
        choose(
            settings["book_dir"]
            if kind == "expert" and not expert_inbox
            else settings["inbox_dir"],
            heading + ("-Draft" if kind == "expert" else "-资料包"),
            "",
            identity,
            claimed,
        )
        if kind == "expert" or len(body) > 1
        else None
    )
    entry = (
        package + "/note.md"
        if package
        else choose(settings["inbox_dir"], heading, ".md", identity, claimed)
    )
    result = {"note.md": entry}
    for logical, raw in files.items():
        if kind == "expert" or (
            package and (logical in body or logical == "publication-notes.json")
        ):
            result[logical] = str(package) + "/" + logical
        elif logical == "note.md":
            result[logical] = entry
        elif logical in supplements:
            result[logical] = choose(
                settings["inbox_dir"],
                heading + "-分析",
                ".md",
                identity + logical,
                claimed | set(result.values()),
            )
        else:
            suffix = PurePosixPath(logical).suffix.lower()
            parent = settings["image_dir"] if suffix in IMAGES else settings["original_dir"]
            # Stable origin suffix plus content digest avoids overwriting distinct sources.
            stem = (
                digest((kind + ":" + identity + ":" + logical).encode())[:16]
                + "-"
                + digest(raw)[:12]
            )
            result[logical] = choose(
                parent, stem, suffix, identity + logical, claimed | set(result.values())
            )
    return entry, package, result, body | supplements.keys(), supplements
