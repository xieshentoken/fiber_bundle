"""Read-only review provenance, bound to the processed task and canonical platform URL."""

import json
import sqlite3
from typing import Any

from .config import IntegrityError, InvalidInput
from .media_files import canonical
from .media_jobs import binding
from .media_link_contract import source_url


def context(db: sqlite3.Connection, job: sqlite3.Row, value: dict[str, Any]) -> dict[str, Any]:
    bound = binding(job)
    source = bound["source"]
    output: dict[str, Any] = {
        "source_sha256": source["sha256"],
        "source_bytes": source["byte_count"],
        "media_type": source["media_type"],
        "duration_ms": value["duration_ms"],
        "platform": None,
    }
    links = db.execute(
        "SELECT * FROM media_link_tasks WHERE job_id=? AND state='complete'", (job["id"],)
    ).fetchall()
    if not links:
        return output
    if len(links) != 1:
        raise IntegrityError("media_review_origin_ambiguous")
    link = links[0]
    try:
        stored = json.loads(link["binding_json"])
        capture = stored["capture"]
        if not isinstance(capture, dict):
            raise IntegrityError("media_review_origin_invalid")
        url = source_url(link["url"])
        version = capture["schema"]
        access = capture["access_state"]
        measured = capture.get("converted_duration_ms")
        declared = link["declared_duration_ms"]
        if (
            link["client_id"] != job["client_id"]
            or link["vault_id"] != job["vault_id"]
            or stored["receipt_id"] != source["receipt_id"]
            or stored["asset_id"] != source["asset_id"]
            or link["mode"] != bound["request"]["evidence_mode"]
            or type(declared) is not int
            or not 1 <= declared <= 60000
            or access not in {"public_acquired", "browser_copy_attested"}
            or version not in {"fiber-media-link-capture/v1", "fiber-media-link-capture/v2"}
        ):
            raise IntegrityError("media_review_origin_changed")
        expected = {
            "schema": version,
            "url": url,
            "mode": link["mode"],
            "declared_duration_ms": declared,
            "source_sha256": source["sha256"],
            "source_bytes": source["byte_count"],
            "media_type": source["media_type"],
            "access_state": access,
            "source_coverage_verified": False,
            "cookies_exported": False,
        }
        if version == "fiber-media-link-capture/v2":
            if type(measured) is not int or measured != value["duration_ms"]:
                raise IntegrityError("media_review_origin_duration_changed")
            expected["converted_duration_ms"] = measured
        elif declared != value["duration_ms"]:
            raise IntegrityError("media_review_origin_duration_changed")
        if canonical(capture) != canonical(expected):
            raise IntegrityError("media_review_origin_changed")
    except (KeyError, TypeError, ValueError, InvalidInput):
        raise IntegrityError("media_review_origin_invalid") from None
    output["platform"] = {
        "url": url,
        "access_state": access,
        "capture_schema": version,
        "declared_duration_ms": declared,
        "converted_duration_ms": measured,
        "source_coverage_verified": False,
        "login_verified": False,
    }
    return output
