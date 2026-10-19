"""Private immutable routing versions and a compare-and-swap current pointer.

Storage lock is held by callers. Files contain credential references, never keys.
"""

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import Config, InvalidInput, contained, make_directory, sync_directory
from .inputs import canonical
from .query_routing_config import MAX_BYTES, QueryRoutingConfig, load, parse
from .query_routing_files import read


def version_path(config: Config, digest: str) -> Path:
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise InvalidInput("routing_digest_invalid")
    return contained(config.state_root, "query-policies/" + digest + ".json")


def legacy_current(config: Config) -> QueryRoutingConfig | None:
    pointer = contained(config.state_root, "query-policy.json")
    if not pointer.exists():
        return None
    try:
        value = json.loads(read(pointer, 1024))
    except ValueError as exc:
        raise InvalidInput("routing_pointer_invalid") from exc
    if not isinstance(value, dict) or set(value) != {"configuration_sha256"}:
        raise InvalidInput("routing_pointer_invalid")
    return version(config, value["configuration_sha256"])


def current(config: Config) -> QueryRoutingConfig | None:
    from .model_configuration import policy as models
    from .query_routing_config import LocalQueryConfig

    prior = legacy_current(config)
    if not models.has_default(config):
        return prior
    selected = models.resolve(config)
    if selected.provider == "local":
        local = LocalQueryConfig(
            **{k: getattr(selected, k) for k in LocalQueryConfig.__dataclass_fields__}
        )
        return QueryRoutingConfig(
            local, prior.cloud_answer if prior else None, prior.approved_backups if prior else ()
        )
    return QueryRoutingConfig(prior.local_query if prior else None, selected, ())


def freeze(config: Config, route: QueryRoutingConfig) -> None:
    """Explicit task submission only; never called from a read-only projection."""
    path = version_path(config, route.digest)
    if not path.exists():
        make_directory(path.parent)
        value = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
        value["approved_backups"] = list(value["approved_backups"])
        write_private(path, canonical(value).encode())


def version(config: Config, digest: str) -> QueryRoutingConfig:
    route = load(version_path(config, digest))
    if route.digest != digest:
        raise InvalidInput("routing_version_changed")
    return route


def inspect(config: Config) -> dict[str, Any]:
    route = current(config)
    if route is None:
        return {
            "configured": False,
            "configuration_sha256": None,
            "value": None,
            "inference_enabled": False,
        }
    value = asdict(route)
    value["approved_backups"] = list(value["approved_backups"])
    return {
        "configured": True,
        "configuration_sha256": route.digest,
        "value": {"schema": "query-routing-preparation/v1", "enabled": False, **value},
        "inference_enabled": False,
    }


def write_private(path: Path, wire: bytes) -> None:
    temp = path.parent / ("." + str(uuid4()) + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(wire)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def save(config: Config, value: object, expected: str | None) -> dict[str, Any]:
    route = parse(value)
    route.validate()
    wire = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if len(wire) > MAX_BYTES:
        raise InvalidInput("routing_configuration_too_large")
    prior = current(config)
    if (None if prior is None else prior.digest) != expected:
        raise InvalidInput("routing_revision_conflict")
    folder = contained(config.state_root, "query-policies")
    make_directory(folder)
    path = version_path(config, route.digest)
    if path.exists():
        version(config, route.digest)
    else:
        write_private(path, wire)
    write_private(
        contained(config.state_root, "query-policy.json"),
        json.dumps({"configuration_sha256": route.digest}).encode(),
    )
    return inspect(config)


def checked(config: Config, digest: str) -> QueryRoutingConfig:
    route = current(config)
    if route is None or route.digest != digest:
        raise InvalidInput("routing_revision_conflict")
    freeze(config, route)
    return route
