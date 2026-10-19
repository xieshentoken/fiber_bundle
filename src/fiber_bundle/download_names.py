"""One bounded filename contract for HTTP downloads and inventory projections."""

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import quote

EXTENSIONS = {
    "text/plain": ".txt",
    "text/uri-list": ".txt",
    "text/html": ".html",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


def filename(name: str, mime: str) -> str:
    value = unicodedata.normalize("NFC", name)
    value = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    value = re.sub(r'[/\\:*?"<>|]', "_", value).strip(" .")[:180] or "original"
    extension = EXTENSIONS.get(mime)
    suffix = PurePosixPath(value).suffix.lower()
    if extension and suffix != extension and not (extension == ".jpg" and suffix == ".jpeg"):
        value = (value[: -len(suffix)] if suffix else value) + extension
    elif not suffix:
        value += ".bin"
    return value


def disposition(name: str) -> str:
    fallback = re.sub(r"[^a-zA-Z0-9._-]", "_", name) or "original.bin"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
