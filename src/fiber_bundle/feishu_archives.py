"""Bounded ZIP member projection in memory; no filesystem extraction or inferred chat IDs."""

from pathlib import PurePosixPath

from .config import InvalidInput
from .inputs import AssetInput
from .weixin_events import MAX_BYTES


def media_type(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


def assets(data: bytes, name: str) -> tuple[AssetInput, ...]:
    if not data or len(data) > MAX_BYTES:
        raise InvalidInput("invalid Feishu attachment bytes")
    if not name.lower().endswith(".zip"):
        return (AssetInput(data, name, media_type(data)),)
    from .archive_members import read

    result = [AssetInput(data, name, "application/zip")]
    for member in read(data):
        path = PurePosixPath(member.path)
        mime, role = media_type(member.data), "attachment"
        if path.suffix.lower() == ".txt":
            mime, role = "text/plain", "body"
        asset = AssetInput(member.data, f"zip-member-{member.index}-{path.name}", mime, role)
        asset.descriptor()
        result.append(asset)
    return tuple(result)
