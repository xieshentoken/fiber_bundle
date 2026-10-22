"""Index metadata scans release the filesystem writer, then validate all input versions."""

from typing import Any

from . import database
from .config import IntegrityError
from .storage_versions import stamp


class Lease:
    def __init__(self, config: Any, vault: str) -> None:
        self.config, self.vault = config, vault
        self.lock = database.locked(config)

    def __enter__(self) -> "Lease":
        self.lock.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self.lock.__exit__(*args)

    def read(self, operation: Any) -> Any:
        before = stamp(self.config, self.vault)
        self.lock.__exit__(None, None, None)
        try:
            value = operation()
        finally:
            self.lock = database.locked(self.config)
            self.lock.__enter__()
        if before != stamp(self.config, self.vault):
            raise IntegrityError("note index source or permission changed")
        return value
