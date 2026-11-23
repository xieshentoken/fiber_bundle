"""A quarantine decision permanently pauses its original unfinished publisher."""

from typing import Any

from ..config import IntegrityError
from . import inbox_review_ledger as ledger


def guard(config: Any, row: Any) -> None:
    if row["state"] == "published":
        return
    paths = set(row["plan"]["mapping"].values()) | set(row["plan"].get("outputs", {}))
    if ledger.quarantined(config, row["vault_id"], paths, row["kind"], row["publication_id"]):
        raise IntegrityError("inbox_review_publication_quarantined")
