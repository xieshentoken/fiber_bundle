"""Private per-channel execution lease and durable, permission-checked query checkpoints."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from .. import database
from ..config import Config, InvalidInput, contained, make_directory
from ..inputs import canonical
from . import store

KEY = "query_processing"
SCHEMA = "channel-query-progress/v1"


@contextmanager
def lease(config: Config, identity: str) -> Iterator[bool]:
    """Serialize the unlocked model work too, including legacy receiver callers."""
    folder = contained(config.state_root, "execution-locks")
    make_directory(folder)
    fd = os.open(
        folder / ("channel-query-" + str(UUID(identity)) + ".lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def initial() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "phase": "prepare",
        "model_configuration_sha256": None,
        "source": None,
    }


def load(data: dict[str, Any]) -> dict[str, Any]:
    saved = data.get(KEY)
    if not isinstance(saved, dict) or saved.get("schema") != SCHEMA:
        # Older interrupted events have no proof of where admission stopped.
        raise InvalidInput("channel_model_result_unknown")
    if saved.get("phase") not in {
        "prepare",
        "retrieval_call",
        "retrieval_validate",
        "evidence",
        "answer_call",
        "source_verify",
    }:
        raise InvalidInput("channel_model_result_unknown")
    digest = saved.get("model_configuration_sha256")
    if digest is not None and (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise InvalidInput("channel_model_result_unknown")
    if digest is None and saved["phase"] != "prepare":
        raise InvalidInput("channel_model_result_unknown")
    source = saved.get("source")
    if source is not None and (
        not isinstance(source, dict)
        or not isinstance(source.get("sources"), list)
        or not isinstance(source.get("retrieval_queries"), list)
    ):
        raise InvalidInput("channel_model_result_unknown")
    return saved


def save(
    config: Config,
    identity: str,
    revision: int,
    event_id: str,
    data: dict[str, Any],
    phase: str,
    digest: str,
    source: Any,
    *,
    independent: bool,
) -> None:
    from . import dispatch

    data[KEY] = {
        "schema": SCHEMA,
        "phase": phase,
        "model_configuration_sha256": digest,
        "source": source,
    }
    data["model_configuration_sha256"] = digest
    with (
        database.locked(config),
        database.connect(config.state_root) as main,
        store.opened(config) as db,
    ):
        dispatch.current(config, main, db, identity, revision, processing=independent)
        if (
            db.execute(
                "UPDATE events SET value=? WHERE channel_id=? AND event_id=? "
                "AND revision=? AND state='processing'",
                (canonical(data), identity, event_id, revision),
            ).rowcount
            != 1
        ):
            raise InvalidInput("channel_scope_withdrawn")
