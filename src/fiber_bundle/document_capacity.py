"""Closed native PDF worker limits; legacy calls retain their original defaults."""

from typing import Any

from .config import InvalidInput

DEFAULT = {
    "source_bytes": 8 * 1024 * 1024,
    "pdf_pages": 100,
    "text_bytes": 1024 * 1024,
    "result_bytes": 4 * 1024 * 1024,
}
CEILING = {**DEFAULT, "source_bytes": 16 * 1024 * 1024, "pdf_pages": 300}
BATCH_PAGES = 25


def checked(value: object | None) -> dict[str, int]:
    if value is None:
        return dict(DEFAULT)
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise InvalidInput("document worker limits")
    for key, maximum in CEILING.items():
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise InvalidInput("document worker capacity")
    return dict(value)


def batches(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import hashlib

    return [
        {
            "first_page": start + 1,
            "last_page": min(start + BATCH_PAGES, len(pages)),
            "text_sha256": hashlib.sha256(
                "".join(p["text"] for p in pages[start : start + BATCH_PAGES]).encode()
            ).hexdigest(),
        }
        for start in range(0, len(pages), BATCH_PAGES)
    ]
