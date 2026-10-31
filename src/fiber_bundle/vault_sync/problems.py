"""Bounded public failure codes; never persist note bodies, provider text or host paths."""

import sqlite3


def reason(error: BaseException) -> str:
    code = str(error)
    known = {
        "cloud_item_not_downloaded": "vault_sync_cloud_pending",
        "cloud_metadata_unavailable": "vault_sync_cloud_metadata_unavailable",
        "cloud_status_check_timeout": "vault_sync_cloud_timeout",
        "cloud_item_pending_stale_or_conflicted": "vault_sync_cloud_unconfirmed",
        "note_index_chunk_limit": "vault_sync_index_capacity",
        "note_index_cancelled": "vault_sync_interrupted",
        "vault_sync_stopping": "vault_sync_interrupted",
        "execution already owned": "vault_sync_index_busy",
    }
    if code in known:
        return known[code]
    if isinstance(error, sqlite3.Error):
        number = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if number == sqlite3.SQLITE_FULL:
            return "vault_sync_index_capacity"
        if number in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "vault_sync_storage_busy"
        return "vault_sync_index_database_error"
    allowed = {
        "vault_sync_revision_conflict",
        "vault_sync_identity_conflict",
        "vault_sync_location_changed",
        "vault_sync_expert_requires_review",
        "vault_sync_replacement_needs_review",
        "vault_sync_source_changed",
        "vault_sync_metadata_changed",
        "vault_sync_note_outside_scope",
        "vault_sync_scope_changed",
        "vault_sync_root_changed",
        "vault_sync_identity_unavailable",
    }
    return code if code in allowed or code in known.values() else "vault_sync_source_unavailable"


def category(code: str) -> str:
    if any(
        part in code for part in ("identity", "review", "location", "revision", "scope", "root")
    ):
        return "review"
    if "cloud" in code:
        return "cloud"
    if code.endswith(("source_changed", "metadata_changed")):
        return "changing"
    return "unavailable"
