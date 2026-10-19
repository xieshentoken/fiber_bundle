"""Stream an imported original while rechecking the current owner and admin session."""

from collections.abc import Iterator

from . import auth, database, document_bindings, source_imports
from .config import Config, InvalidInput
from .document_source import CHUNK_BYTES, DocumentFile


def chunks(
    config: Config, identity: str, client_id: str, file: DocumentFile, session: str
) -> Iterator[bytes]:
    with file.open() as stream:
        while True:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    auth.admin(db, session)
                    source = source_imports.get(config, db, client_id, identity)
                    current = document_bindings.source(
                        config,
                        db,
                        client_id,
                        source["receipt_id"],
                        source["asset_id"],
                        verify_file=False,
                    ).file
                    if current != file:
                        raise InvalidInput("import_original_changed")
                    data = stream.read(CHUNK_BYTES)
                finally:
                    db.close()
            if not data:
                return
            yield data
