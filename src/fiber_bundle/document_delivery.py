"""Bounded original delivery, with current session and source grants checked per chunk."""

from collections.abc import Iterator

from . import auth, database, document_bindings
from .config import Config, InvalidInput
from .document_source import CHUNK_BYTES, DocumentFile


def chunks(
    config: Config, run_id: str, client_id: str, source: DocumentFile, session: str
) -> Iterator[bytes]:
    with source.open() as stream:
        while True:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    auth.admin(db, session)
                    run = db.execute(
                        "SELECT * FROM execution_runs WHERE id=? AND client_id=?",
                        (run_id, client_id),
                    ).fetchone()
                    task = document_bindings.get(db, run_id)
                    if (
                        run is None
                        or task is None
                        or run["state"] != "succeeded"
                        or task["state"] != "extracted"
                    ):
                        raise InvalidInput("document original unavailable")
                    auth.grant(db, client_id, run["vault_id"], "original")
                    current = document_bindings.authorize(
                        config, db, run, task, permission="query", verify_file=False
                    )
                    if current.file != source:
                        raise InvalidInput("document original changed")
                    data = stream.read(CHUNK_BYTES)
                    if not data:
                        return
                finally:
                    db.close()
            yield data
