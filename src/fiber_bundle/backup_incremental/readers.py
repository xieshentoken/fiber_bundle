"""Expose existing restoration's bounded record interface for both backup generations."""

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from ..backup_bundle import file_sha, section_entries
from ..backup_crypto import CHUNK, Records
from ..config import IntegrityError
from ..inputs import canonical
from . import objects, snapshot
from .repository import Repository, regular


class ObjectReader:
    def __init__(self, repo: Repository, value: dict[str, Any], part: str):
        self.repo, self.value = repo, value
        manifest = value["manifest"]
        files = [entry for entry in manifest["entries"] if entry["kind"] == "file"]
        references = {
            (e["section"], e["path"]): ref
            for e, ref in zip(files, value["references"], strict=True)
        }
        self.entries = [
            (e, references[(e["section"], e["path"])])
            for e in section_entries(manifest, part)
            if e["kind"] == "file"
        ]
        self.payload = [(entry, identity) for entry, identity in self.entries if entry["bytes"]]
        self.header = True
        self.index = 0
        self.remaining = 0
        self.handle: Any = None
        self.reader: Records | None = None

    def get(self) -> bytes:
        if self.header:
            self.header = False
            return canonical(self.value["manifest"]).encode()
        if self.reader is None:
            if self.index >= len(self.payload):
                raise IntegrityError("restore_file_frame_invalid")
            entry, identity = self.payload[self.index]
            path = self.repo.path(identity)
            try:
                regular(path)
            except FileNotFoundError as exc:
                raise IntegrityError("backup_incremental_object_missing") from exc
            if file_sha(path) != self.value["objects"][identity]["cipher_sha256"]:
                raise IntegrityError("backup_ciphertext_changed")
            self.handle = path.open("rb")
            self.reader = self.repo.records(self.handle, write=False)
            if json.loads(self.reader.get()) != {
                "id": identity,
                "bytes": entry["bytes"],
                "sha256": entry["sha256"],
            }:
                raise IntegrityError("backup_incremental_object_invalid")
            self.remaining = entry["bytes"]
        data = self.reader.get()
        if len(data) != min(CHUNK, self.remaining):
            raise IntegrityError("restore_file_frame_invalid")
        self.remaining -= len(data)
        if not self.remaining:
            self.reader.finish(self.payload[self.index][0]["sha256"])
            self.close()
            self.index += 1
        return data

    def finish(self, sha: str) -> None:
        if (
            self.header
            or self.index != len(self.payload)
            or self.reader is not None
            or sha != self.value["manifest"]["sha256"]
        ):
            raise IntegrityError("backup_final_record_invalid")
        for entry, identity in self.entries:
            if not entry["bytes"]:
                objects.check(
                    self.repo, identity, entry, self.value["objects"][identity], full=True
                )

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
        self.handle = self.reader = None


@contextmanager
def sections(bundle: Path, password: str, marker: dict[str, Any]) -> Iterator[tuple[Any, Any]]:
    with ExitStack() as stack:
        if marker["schema"] == snapshot.SCHEMA:
            repo, value = snapshot.read(bundle, password)
            left, right = ObjectReader(repo, value, "state"), ObjectReader(repo, value, "vaults")
            stack.callback(left.close)
            stack.callback(right.close)
            yield left, right
        else:
            state = stack.enter_context((bundle / "state.fbenc").open("rb"))
            vault = stack.enter_context((bundle / "vaults.fbenc").open("rb"))
            yield Records(state, password, write=False), Records(vault, password, write=False)
