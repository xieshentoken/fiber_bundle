"""Private connection files. Paths are derived from validated identities, never model input."""

import json
import re
from pathlib import Path
from uuid import UUID

from ..config import Config, InvalidInput, contained, make_directory
from ..query_policy_store import write_private
from ..query_routing_files import read


def folder(config: Config, identity: str, *, secret: bool = False) -> Path:
    try:
        if str(UUID(identity)) != identity:
            raise ValueError()
    except ValueError:
        raise InvalidInput("agent_identity_invalid") from None
    name = "agent-connection-secrets" if secret else "agent-connections"
    return contained(config.state_root, name + "/" + identity)


def put(config: Config, identity: str, name: str, value: object, *, secret: bool = False) -> Path:
    home = folder(config, identity, secret=secret)
    make_directory(home.parent)
    make_directory(home)
    path = contained(home, name)
    write_private(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
    return path


def private_json(path: Path, limit: int = 65536) -> object:
    return json.loads(read(path, limit, private=True))


def credential(config: Config, identity: str) -> str:
    value = private_json(folder(config, identity, secret=True) / "credential.json", 1024)
    if not isinstance(value, dict) or set(value) != {"token"}:
        raise InvalidInput("agent_credential_invalid")
    token = value["token"]
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 128
        or not re.fullmatch(r"[A-Za-z0-9_-]+", token)
    ):
        raise InvalidInput("agent_credential_invalid")
    return token
