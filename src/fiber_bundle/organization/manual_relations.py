"""Snapshot-local lookup of human MOC links; no filesystem or persistent cache."""

import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any

from ..moc_markdown import split
from ..vendor import knowledge_organizer_v4 as upstream


class ManualRelations:
    def __init__(self, mocs: Any):
        self.targets: dict[str, set[str]] = defaultdict(set)
        self.backlinks: dict[str, set[str]] = defaultdict(set)
        self.explicit: dict[str, set[str]] = defaultdict(set)
        stems = Counter(PurePosixPath(m["path"]).stem for m in mocs)
        for moc in mocs:
            mid = moc["id"]
            self.targets[moc["path"].removesuffix(".md")].add(mid)
            stem = PurePosixPath(moc["path"]).stem
            if stems[stem] == 1:
                self.targets[stem].add(mid)
            for entity, owners in moc["members"].items():
                if "manual" in owners:
                    self.explicit[entity].add(mid)
            prefix, _, suffix = split(moc["text"].encode(), mid)
            for target in self.links((prefix + suffix).decode()):
                self.backlinks[self.target(target)].add(mid)

    @staticmethod
    def links(text: str) -> list[str]:
        return re.findall(r"(?<!!)\[\[([^\[\]\n]+)\]\]", text)

    @staticmethod
    def target(value: str) -> str:
        return value.split("|")[0].split("#")[0].strip().removesuffix(".md")

    def memberships(self, entity: Any) -> list[str]:
        text = entity.get("_manual_text", entity["text"])
        front, _, _ = upstream.split_document(text)
        values = upstream.normalize_list(upstream.parse_frontmatter(front).get("moc"))
        found = set(self.explicit.get(entity["id"], ()))
        for target in [*self.links(text), *values]:
            found.update(self.targets.get(self.target(target), ()))
        if entity["kind"] == "note":
            for target in (entity["path"].removesuffix(".md"), PurePosixPath(entity["path"]).stem):
                found.update(self.backlinks.get(target, ()))
        return sorted(found)
