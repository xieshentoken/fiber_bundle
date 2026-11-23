"""Lossless primary-window reconstruction and bounded legacy MOC classification."""

import re
from typing import Any

from .. import note_index, note_search_cache
from ..config import Config, IntegrityError
from ..inputs import canonical
from ..note_transfer_links import mask


def reconstruct(rows: Any) -> str | None:
    """Primary windows precede adjacent passages in both existing index writers.

    Keep physical line numbers and verify every 80-character overlap. Parsing
    isolated snippets would turn fenced code examples into false graph edges.
    """
    lines: dict[int, str] = {}
    previous = 0
    offset = 0
    for number, passage in rows:
        if "\n" in passage or "\r" in passage or number < previous:
            break  # Remaining rows are adjacent passages, not primary windows.
        if number < 1 or number > note_index.Limits().note_bytes:
            return None
        if number != previous:
            offset = 0
            lines[number] = passage
        else:
            offset += 320
            old = lines[number]
            overlap = len(old) - offset
            if not 1 <= overlap <= 80 or old[offset:] != passage[:overlap]:
                return None
            lines[number] = old[:offset] + passage
        previous = number
    if not lines:
        return ""
    return "\n".join(lines.get(number, "") for number in range(1, max(lines) + 1))


def moc_paths(config: Config, vault: str, paths: set[str]) -> tuple[set[str], bool]:
    if not paths:
        return set(), True
    found: set[str] = set()
    processed: set[str] = set()
    used = chunks = 0
    complete = True

    def consume(path: str, rows: list[tuple[int, str]]) -> None:
        nonlocal used, complete
        if path in processed:
            raise IntegrityError("note cloud index chunks invalid")
        processed.add(path)
        if used >= 64 * 1024 * 1024:
            complete = False
            return
        text = reconstruct(rows)
        if text is None:
            complete = False
            return
        used += len(text.encode())
        if used > 64 * 1024 * 1024:
            complete = False
            return
        if re.search(r"(?im)^type:\s*moc\s*$", mask(text)):
            found.add(path)

    with note_search_cache.connection(config, vault) as index:
        if index is None:
            return set(), False
        current = None
        rows: list[tuple[int, str]] = []
        previous = 0
        done = False
        for row in index.execute(
            "SELECT path,line,snippet FROM chunks WHERE path IN "
            "(SELECT value FROM json_each(?)) ORDER BY rowid",
            (canonical(sorted(paths)),),
        ):
            chunks += 1
            if chunks > 500_000:
                complete = False
                break
            if row["path"] != current:
                if current is not None:
                    consume(current, rows)
                current, rows, previous, done = row["path"], [], 0, False
            if not done:
                text = row["snippet"]
                done = "\n" in text or "\r" in text or row["line"] < previous
                if not done:
                    rows.append((row["line"], text))
                    previous = row["line"]
        else:
            if current is not None:
                consume(current, rows)
    return found, complete
