"""Bounded explicit ZIP input, extracted into one private, immutable worker input tree."""

import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .config import InvalidInput, make_directory, sync_directory
from .document_source import DocumentFile


def unpack(file: DocumentFile, root: Path) -> None:
    with file.open() as stream, zipfile.ZipFile(stream) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if (
            not 1 <= len(names) <= 2000
            or len(names) != len(set(names))
            or "workpack.json" not in names
            or sum(m.file_size for m in members) > 32 * 1024 * 1024
        ):
            raise InvalidInput("expert_workpack_scope")
        for item in members:
            path = PurePosixPath(item.filename)
            if (
                path.is_absolute()
                or path.as_posix() != item.filename.rstrip("/")
                or any(p in {"", ".", ".."} for p in item.filename.rstrip("/").split("/"))
                or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
                or "\\" in item.filename
                or len(item.filename) > 1024
                or any(p in {".", ".."} or p.startswith(".") for p in path.parts)
                or item.flag_bits & 1
                or stat.S_ISLNK(item.external_attr >> 16)
                or len(path.parts) > 12
                or not 0 <= item.file_size <= 8 * 1024 * 1024
                or (
                    not item.is_dir()
                    and path.suffix.lower()
                    not in {".json", ".jsonl", ".txt", ".md", ".png", ".jpg", ".jpeg"}
                )
            ):
                raise InvalidInput("expert_workpack_member")
        make_directory(root)
        for item in members:
            target = root / item.filename
            if item.is_dir():
                make_directory(target)
                continue
            make_directory(target.parent)
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as output, archive.open(item) as source:
                total = 0
                while data := source.read(256 * 1024):
                    total += len(data)
                    if total > item.file_size:
                        raise InvalidInput("expert_workpack_member_changed")
                    output.write(data)
                if total != item.file_size:
                    raise InvalidInput("expert_workpack_member_changed")
                output.flush()
                os.fsync(output.fileno())
            sync_directory(target.parent)
