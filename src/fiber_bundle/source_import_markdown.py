"""Literal Markdown preservation and missing-reference diagnostics; never read host paths."""

from urllib.parse import unquote, urlsplit

from .note_transfer_links import ATTRIBUTES, MARKDOWN, REFERENCE, TAGS, WIKI, mask


def references(text: str) -> list[str]:
    visible = mask(text)
    found: set[str] = set()
    for pattern in (MARKDOWN, REFERENCE, WIKI):
        for match in pattern.finditer(visible):
            raw = match[1] or match[2]
            path = raw.split("|", 1)[0].split("#", 1)[0]
            if not path:
                continue
            # Preserve normal remote note links; external embedded images are not fetched.
            if not urlsplit(path).scheme and not path.startswith("//"):
                found.add(unquote(path))
            elif match[0].startswith("!"):
                found.add(path)
    for tag in TAGS.finditer(visible):
        for attribute in ATTRIBUTES.finditer(tag[0]):
            if attribute[1].lower() in {"src", "data", "href"}:
                value = attribute[2] or attribute[3]
                if value and not value.startswith("#"):
                    found.add(value)
    return sorted(found)
