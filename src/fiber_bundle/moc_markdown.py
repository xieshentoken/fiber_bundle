"""Splice only a verified managed block; all legacy MOC bytes remain human-owned."""

import os
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from . import moc_associations, note_inventory
from .config import IntegrityError
from .moc_store import Record


def markers(moc_id: str) -> tuple[bytes, bytes]:
    return (
        f"<!-- fiber-moc:v1 {moc_id} begin -->".encode(),
        f"<!-- fiber-moc:v1 {moc_id} end -->".encode(),
    )


def split(data: bytes, moc_id: str) -> tuple[bytes, bytes, bytes]:
    begin, end = markers(moc_id)
    if b"<!-- fiber-moc:" not in data:
        return data, b"", b""
    if data.count(b"<!-- fiber-moc:") != 2 or data.count(begin) != 1 or data.count(end) != 1:
        raise IntegrityError("MOC managed markers conflict")
    start, stop = data.index(begin), data.index(end) + len(end)
    if stop <= start or (start and data[start - 1 : start] != b"\n"):
        raise IntegrityError("MOC managed marker layout invalid")
    if data[stop : stop + 2] == b"\r\n":
        stop += 2
    elif data[stop : stop + 1] == b"\n":
        stop += 1
    return data[:start], data[start:stop], data[stop:]


def manual_paths(data: bytes, inventory: note_inventory.Inventory) -> set[str]:
    # Only unambiguous existing wikilinks suppress an additional system link.
    notes = inventory.notes
    paths: set[str] = set()
    text = re.sub(r"<!--.*?-->", "", data.decode("utf-8-sig"), flags=re.DOTALL)
    visible = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence is None:
            visible.append(re.sub(r"`[^`]*`", "", line))
    for target in re.findall(r"(?<!!)\[\[([^\[\]\n]+)\]\]", "\n".join(visible)):
        target = target.split("|", 1)[0].split("#", 1)[0].strip().removesuffix(".md")
        matches = [
            n.relative_path
            for n in notes
            if n.relative_path.removesuffix(".md") == target
            or ("/" not in target and PurePosixPath(n.relative_path).stem == target)
        ]
        if len(matches) == 1:
            paths.add(matches[0])
    return paths


def render(
    data: bytes,
    moc_id: str,
    relative: str,
    record: Record,
    inventory: note_inventory.Inventory,
) -> tuple[bytes, bytes, int]:
    prefix, old, suffix = split(data, moc_id)
    human = manual_paths(prefix + suffix, inventory)
    notes = {
        n.identity or ("publication:" + n.publication_id if n.publication_id else ""): n
        for n in inventory.notes
        if n.identity or n.publication_id
    }
    newline = "\r\n" if b"\r\n" in data else "\n"
    begin, end = markers(moc_id)
    lines = [begin.decode(), "## 系统增量关联", ""]
    preserved = 0
    for identity, owners in sorted(record.members.items()):
        note = notes.get(identity)
        if identity.startswith("expert:"):
            lines.append(f"- 专家 Skill：`{identity}`（通过 Fiber Agent 接口调用）")
        elif note is None:
            lines.append(f"- 当前不可见：{identity}（归属保留）")
        elif note.relative_path in human:
            preserved += 1
        else:
            destination = os.path.relpath(note.relative_path, str(PurePosixPath(relative).parent))
            label = moc_associations.escape(PurePosixPath(note.relative_path).name)
            ownership = "、".join("人工登记" if o == "manual" else "系统" for o in owners)
            lines.append(f"- [{label}](<{quote(destination, safe='/')}>)（{ownership}）")
    if len(lines) == 3:
        lines.append("暂无额外关联；已有人工链接保持。")
    lines.extend([end.decode(), ""])
    block = newline.join(lines).encode()
    if not old:
        separator = b"" if not data or data.endswith(b"\n\n") else newline.encode() * 2
        prefix += separator
    result = prefix + block + suffix
    if len(result) > 1024 * 1024:
        raise IntegrityError("MOC file exceeds note limit")
    return result, block, preserved
