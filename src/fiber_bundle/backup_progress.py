"""Optional progress contract shared by backup IO; no persistence or secret data."""

from collections.abc import Callable
from typing import Any

from .config import IntegrityError

Progress = Callable[[dict[str, Any]], None]


def report(callback: Progress | None, phase: str, **values: Any) -> None:
    if callback is not None:
        callback({"phase": phase, **values})


class SourceChanged(IntegrityError):
    """Keep the stable public error code and a bounded, relative diagnostic."""

    def __init__(self, section: str, path: str):
        super().__init__("backup_source_changed")
        self.details = {"section": section[:100], "path": path[:2048]}
