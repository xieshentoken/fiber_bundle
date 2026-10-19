"""Edit only the moc frontmatter field; preserve all other Markdown bytes."""

import json
import re
from collections.abc import Callable

from ..config import IntegrityError


def target(value: str) -> str:
    value = value.strip()
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2]
    return value.split("|")[0].split("#")[0].strip().removesuffix(".md")


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        try:
            value, end = json.JSONDecoder().raw_decode(raw)
            suffix = raw[end:].strip()
            if not isinstance(value, str) or (suffix and not suffix.startswith("#")):
                raise ValueError()
            return value
        except ValueError as error:
            raise IntegrityError("note_moc_unsupported_yaml") from error
    if raw.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", raw)
        if match:
            return match[1].replace("''", "'")
        raise IntegrityError("note_moc_unsupported_yaml")
    raw = re.split(r"\s+#", raw, maxsplit=1)[0].rstrip()
    if raw in {"null", "~"}:
        return ""
    if raw.startswith(("{", "&", "*", "!", "|", ">")) or "\n" in raw:
        raise IntegrityError("note_moc_unsupported_yaml")
    return raw


def _values(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return []
    if raw.startswith("[") and not raw.startswith("[["):
        # YAML flow sequences of strings, with quoted commas and Obsidian links.
        parts, current, quote = [], "", ""
        index = 1
        while index < len(raw):
            char = raw[index]
            if quote:
                current += char
                if char == "\\" and quote == '"':
                    index += 1
                    if index < len(raw):
                        current += raw[index]
                elif char == quote:
                    if quote == "'" and raw[index : index + 2] == "''":
                        current += "'"
                        index += 1
                    else:
                        quote = ""
            elif char in {"'", '"'}:
                quote = char
                current += char
            elif char == ",":
                parts.append(_scalar(current))
                current = ""
            elif char == "]":
                parts.append(_scalar(current))
                if raw[index + 1 :].strip() and not raw[index + 1 :].strip().startswith("#"):
                    break
                return [v for v in parts if v]
            else:
                current += char
            index += 1
        raise IntegrityError("note_moc_unsupported_yaml")
    return [v for v in [_scalar(raw)] if v]


def field(text: str) -> tuple[int, int, list[str], str, bool]:
    """Return exact field span, values, newline and whether frontmatter exists."""
    newline = "\r\n" if "\r\n" in text else "\n"
    match = re.match(r"\ufeff?---\r?\n", text)
    if not match:
        if text.lstrip("\ufeff").startswith("---"):
            raise IntegrityError("note_moc_invalid_frontmatter")
        return len(text) - len(text.lstrip("\ufeff")), 0, [], newline, False
    close = re.search(r"(?m)^(?:---|\.\.\.)\s*\r?$", text[match.end() :])
    if close is None:
        raise IntegrityError("note_moc_invalid_frontmatter")
    end = match.end() + close.start()
    front = text[match.end() : end]
    found = list(re.finditer(r"(?m)^(?:moc|'moc'|\"moc\"):[ \t]*(.*)\r?$", front))
    if len(found) > 1:
        raise IntegrityError("note_moc_duplicate_field")
    if not found:
        return end, end, [], newline, True
    item = found[0]
    start = match.end() + item.start()
    stop = match.end() + item.end()
    if text[stop : stop + len(newline)] == newline:
        stop += len(newline)
    values = _values(item[1].rstrip("\r"))
    # Indented block sequences, comments and blank lines belong to this field.
    while stop < end:
        line_end = text.find("\n", stop, end)
        line_end = end if line_end < 0 else line_end + 1
        line = text[stop:line_end].rstrip("\r\n")
        member = re.match(r"^[ \t]*-\s+(.*)$", line)
        if member:
            values.extend(_values(member[1]))
        elif line.strip() and not line.lstrip().startswith("#"):
            if line.startswith((" ", "\t")):
                raise IntegrityError("note_moc_unsupported_yaml")
            break
        stop = line_end
    return start, stop, values, newline, True


def render(
    raw: bytes,
    desired: list[str],
    owned: list[str],
    resolve: Callable[[str], str] = target,
) -> tuple[bytes, list[str]]:
    text = raw.decode("utf-8")
    start, stop, values, newline, had = field(text)
    kept, seen = [], set()
    wanted = {resolve(v) for v in desired}
    for value in values:
        if value in owned and resolve(value) not in wanted:
            continue
        key = resolve(value)
        if key not in seen:
            kept.append(value)
            seen.add(key)
    inserted = []
    for value in desired:
        if resolve(value) not in seen:
            inserted.append(value)
            seen.add(resolve(value))
    final = kept + inserted
    managed = [v for v in kept if v in owned] + inserted
    # Do not reformat an existing field merely to record ownership or deduplicate a no-op.
    if final == values:
        return raw, managed
    comments = [line for line in text[start:stop].splitlines() if line.lstrip().startswith("#")]
    inline = re.search(r"\s+(#.*)", text[start:stop].splitlines()[0]) if start < stop else None
    if inline:
        comments.insert(0, inline[1])
    block = (
        (
            "moc:"
            + newline
            + "".join("  - " + json.dumps(v, ensure_ascii=False) + newline for v in final)
        )
        if final
        else ""
    )
    block += "".join(comment + newline for comment in comments)
    if not had:
        if not final:
            return raw, managed
        return (
            text[:start] + "---" + newline + block + "---" + newline + text[start:]
        ).encode(), managed
    return (text[:start] + block + text[stop:]).encode(), managed


def human_text(text: str, owned: list[str]) -> str:
    return render(text.encode(), [], owned)[0].decode() if owned else text
