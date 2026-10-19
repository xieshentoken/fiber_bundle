"""Mark every retained manifest before sweeping authenticated unreferenced shared objects."""

import json
import re
from pathlib import Path
from typing import Any

from ..backup_inventory import signature
from ..config import Config, IntegrityError, InvalidInput, sync_directory
from . import objects, snapshot
from .repository import Repository, digest, regular


def collect(config: Config, root: Path, state: dict[str, Any]) -> int:
    from ..model_configuration import keychain

    namespaces: dict[str, tuple[Repository, set[str]]] = {}
    jobs = [
        j
        for j in state["jobs"]
        if j.get("backup_format") == snapshot.SCHEMA and Path(j["destination"]).parent == root
    ]
    for job in jobs:
        namespace = job["namespace"]
        if namespace not in namespaces:
            password = keychain.get(job.get("secret_account", state["account"])).hex()
            namespaces[namespace] = (Repository(root, password, namespace), set())
        if job["state"] not in {"succeeded", "deleting", "cleanup_interrupted"}:
            continue
        if job["state"] != "succeeded":
            # Incomplete snapshot deletion must finish before shared data is reclaimed.
            raise InvalidInput("backup_retention_needs_attention")
        password = keychain.get(job.get("secret_account", state["account"])).hex()
        snapshot.verify(Path(job["destination"]), password, full=False)
        _, value = snapshot.read(Path(job["destination"]), password)
        namespaces[namespace][1].update(value["references"])
    removed = 0
    for repo, referenced in namespaces.values():
        paths = list(repo.home.iterdir())
        for path in paths:
            regular(path)
            if path.name != "repository.json" and not re.fullmatch(
                r"(?:[a-f0-9]{64}\.fbobj|\.pending-[a-f0-9-]{36})", path.name
            ):
                raise InvalidInput("backup_repository_unexpected_content")
        for path in paths:
            if path.name == "repository.json" or re.fullmatch(
                r"\.pending-[a-f0-9-]{36}", path.name
            ):
                continue
            if not re.fullmatch(r"[a-f0-9]{64}\.fbobj", path.name):
                raise InvalidInput("backup_repository_unexpected_content")
            identity = digest(path.stem)
            if identity in referenced:
                continue
            before = regular(path)
            with path.open("rb") as handle:
                reader = repo.records(handle, write=False)
                entry = json.loads(reader.get())
                if (
                    repo.identity(entry["sha256"], entry["bytes"]) != identity
                    or entry["id"] != identity
                ):
                    raise IntegrityError("backup_incremental_object_invalid")
            from ..backup_bundle import file_sha

            objects.check(repo, identity, entry, {"cipher_sha256": file_sha(path)}, full=True)
            if signature(path.lstat()) != signature(before):
                raise IntegrityError("backup_repository_changed")
            path.unlink()
            removed += before.st_size
        sync_directory(repo.home)
    return removed
