"""Pure metadata adapter for pinned organizer V4. Never call its filesystem/apply commands."""

from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..vendor import knowledge_organizer_v4 as upstream

CONFIG: Any = upstream.DEFAULT_CONFIG


def note(entity: Any) -> Any:
    text = entity.get("_manual_text", entity["text"])
    front, body, had = upstream.split_document(text)
    data = upstream.parse_frontmatter(front)
    data["title"] = entity["title"]
    data["tags"] = sorted(
        set(upstream.normalize_list(data.get("tags"))) | set(entity.get("tags", []))
    )
    path = Path(entity["path"])
    return upstream.Note(path, path, text, front, data, body or "\n", had)


def hubs(mocs: Any) -> Any:
    result = []
    for m in mocs:
        front, body, had = upstream.split_document(m["text"])
        data = upstream.parse_frontmatter(front)
        topics = set()
        for field in ("tags", "topics", "aliases"):
            for value in upstream.normalize_list(data.get(field)):
                topics.update(upstream.topic_variants(value))
        topics.update(
            upstream.topic_variants(
                upstream.clean_hub_title(m["title"], CONFIG["hub_detection"]["filename_suffixes"])
            )
        )
        # IDs replace name keys so same-titled MOCs remain distinct.
        path = Path(m["id"] + ".md")
        n = upstream.Note(
            path, path, m["text"], front, OrderedDict(title=m["title"]), body or "\n", had
        )
        result.append(upstream.Hub(n, {v.casefold() for v in topics}, set()))
    return result


def infer(entity: Any, mocs: Any) -> list[dict[str, Any]]:
    return upstream.infer_hubs(note(entity), hubs(mocs), upstream.DEFAULT_CONFIG, [])


def taxonomy(entities: Any, mocs: Any, plans: Any) -> Any:
    projected = {p["path"]: ["[[" + mid + "]]" for mid in p["selected"]] for p in plans}
    root = Path("/fiber-authorized-metadata")
    policy = upstream.Policy(root, (), (), "")
    return upstream.build_taxonomy_report(
        root,
        [note(e) for e in entities],
        hubs(mocs),
        upstream.DEFAULT_CONFIG,
        policy,
        projected_mocs=projected,
    )
