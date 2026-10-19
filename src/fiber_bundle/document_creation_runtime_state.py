"""Persistent runtime transcript integrity; independent of orchestration and owner objects."""

import json
import sqlite3
from typing import Any

from . import document_creation_dialogue as dialogue
from . import document_creation_profile as profile
from .config import IntegrityError, InvalidInput
from .document_creation_config import CreationExecutionConfig
from .document_creation_output import FORMAT_REVISION, FORMATS
from .inputs import canonical
from .objects import digest


def rows(db: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM document_creation_calls WHERE run_id=? ORDER BY ordinal", (run_id,)
    ).fetchall()


def checked(
    db: sqlite3.Connection,
    bound: sqlite3.Row,
    policy: CreationExecutionConfig,
    calls: list[sqlite3.Row],
) -> None:
    session = db.execute(
        "SELECT * FROM document_creation_sessions WHERE run_id=?", (bound["run_id"],)
    ).fetchone()
    records = rows(db, bound["run_id"])
    artifact = db.execute(
        "SELECT * FROM document_creation_artifacts WHERE run_id=?", (bound["run_id"],)
    ).fetchone()
    try:
        if session is None:
            if len(calls) != 1 or records or artifact is not None:
                raise ValueError()
            return
        declared = profile.value(
            session["mode"], registered=hasattr(policy.policy(), "configuration_id")
        )
        expected = canonical(declared)
        if (
            session["profile_json"] != expected
            or session["profile_sha256"] != digest(expected.encode())
            or session["mode"] not in {"synthetic", "provider"}
            or policy.policy().image != declared["image"]
            or policy.capability_revision != declared["revision"]
        ):
            raise ValueError()
        if not 1 <= len(calls) <= 2 or (
            len(records) != len(calls) and not (not records and len(calls) == 1)
        ):
            raise ValueError()
        fees = {c["id"]: c for c in calls}
        prior = None
        for ordinal, record in enumerate(records, 1):
            call = fees[record["call_id"]]
            if record["ordinal"] != ordinal or call["run_id"] != bound["run_id"]:
                raise ValueError()
            payload = json.loads(record["request_json"])
            if (
                canonical(payload) != record["request_json"]
                or digest(record["request_json"].encode()) != record["request_sha256"]
            ):
                raise ValueError()
            if (
                canonical(dialogue.request(policy.policy(), payload, bound["request_json"], prior))
                != record["request_json"]
            ):
                raise ValueError()
            body = None
            if record["response_json"] is not None:
                body = json.loads(record["response_json"])
                if (
                    canonical(body) != record["response_json"]
                    or digest(record["response_json"].encode()) != record["response_sha256"]
                    or call["state"] != "settled"
                ):
                    raise ValueError()
                dialogue.response(policy.policy(), body, ordinal)
                if not isinstance(body.get("usage"), dict):
                    raise ValueError()
                if any(
                    type(body["usage"].get(k)) is not int or body["usage"][k] != call[column]
                    for k, column in (
                        ("prompt_tokens", "input_tokens"),
                        ("completion_tokens", "output_tokens"),
                    )
                ):
                    raise ValueError()
            if ordinal == 1 and len(records) == 2 and body is None:
                raise ValueError()
            prior = (payload, body) if body is not None else None
        if artifact is not None:
            if artifact["version"] != 1:
                raise ValueError()
            expected_manifest = manifest(bound, session, records, calls)
            raw = artifact["artifact_json"]
            fmt = expected_manifest["artifact"]["original_name"]
            if (
                raw != canonical(expected_manifest)
                or digest(raw.encode()) != artifact["artifact_sha256"]
                or artifact["relative_path"] != "document-drafts/" + bound["run_id"] + "/" + fmt
            ):
                raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, InvalidInput):
        raise IntegrityError("document_runtime_binding_invalid") from None


def summary(db: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    session = db.execute(
        "SELECT * FROM document_creation_sessions WHERE run_id=?", (run_id,)
    ).fetchone()
    if session is None:
        return None
    result = {
        "mode": session["mode"],
        "profile_sha256": session["profile_sha256"],
        "round_count": len(rows(db, run_id)),
        "real_provider_calls": len(rows(db, run_id)) if session["mode"] == "provider" else 0,
    }
    return result


def manifest(
    bound: sqlite3.Row, session: sqlite3.Row, records: list[sqlite3.Row], calls: list[sqlite3.Row]
) -> dict[str, Any]:
    if (
        len(records) != 2
        or any(r["response_json"] is None for r in records)
        or len(calls) != 2
        or any(c["state"] != "settled" for c in calls)
    ):
        raise InvalidInput("document_draft_rounds_incomplete")
    _, content, _ = dialogue.draft(json.loads(records[0]["response_json"]))
    fmt = json.loads(json.loads(bound["request_json"])["messages"][1]["content"])[
        "requested_format"
    ]
    accounting = [
        {
            k: call[k]
            for k in (
                "id",
                "billing_day",
                "model",
                "price_revision",
                "currency",
                "reserved_nusd",
                "charged_nusd",
                "input_tokens",
                "output_tokens",
                "state",
            )
        }
        for call in sorted(calls, key=lambda c: c["id"])
    ]
    return {
        "schema": "document-creation-draft-version/v1",
        "run_id": bound["run_id"],
        "version": 1,
        "plan_revision": bound["revision"],
        "plan_sha256": bound["plan_sha256"],
        "input_sha256": bound["input_sha256"],
        "configuration_sha256": bound["configuration_sha256"],
        "profile_sha256": session["profile_sha256"],
        "request_sha256": [r["request_sha256"] for r in records],
        "accounting_sha256": digest(canonical(accounting).encode()),
        "artifact": {
            "original_name": "draft." + fmt,
            "media_type": FORMATS[fmt],
            "sha256": digest(content),
            "byte_count": len(content),
        },
        "format_check_revision": FORMAT_REVISION,
        "format_verified": True,
        "input_coverage": "retrieved_excerpts",
        "execution_mode": session["mode"],
        "real_provider_calls": len(records) if session["mode"] == "provider" else 0,
        "unverified_draft": True,
        "semantic_verified": False,
        "knowledge_verified": False,
        "uploadable_verified": False,
        "delivery_allowed": False,
        "save_allowed": False,
    }
