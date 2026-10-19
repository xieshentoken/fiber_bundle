#!/usr/bin/env python3
"""Record browser-verified access state without accepting authentication secrets."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import (
    ACCESS_STATES,
    AUTHORIZED_ACCESS_STATES,
    canonical_access_state,
    legacy_access_status,
    update_pipeline_state,
    utc_now,
    write_json,
)


SECRET_VALUE_RE = re.compile(
    r"sessdata\s*=|cookie\s*:|authorization\s*:|bearer\s+|"
    r"(?:access[_-]?token|refresh[_-]?token|signed[_-]?url|token|signature)\s*[=:]"
    r"|https?://[^\s]+(?:token|sign|auth|expires|key)=",
    re.I,
)

STATUS_CHOICES = tuple(
    sorted(
        {
            "public",
            "public_verified",
            "authenticated",
            "member_authorized",
            "auth_required",
            "denied",
            "drm_blocked",
            "unknown",
        }
    )
)
BACKENDS = ("edge", "chrome", "generic_browser", "manual")


def canonical_from_cli(value: str) -> str:
    if value not in ACCESS_STATES and value != "public":
        raise ValueError(f"Unsupported access state: {value}")
    return "public_verified" if value == "public" else value


def build_access_record(
    status: str,
    verified_by: str,
    note: str,
    *,
    browser_backend: str | None = None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = canonical_from_cli(status)
    access = dict(prior or {})
    access.update(
        {
            "schema_version": "2.0",
            "status": legacy_access_status(state),
            "access_state": state,
            "verified_at": utc_now(),
            "verified_by": verified_by,
            "entitlement": "authorized" if state in AUTHORIZED_ACCESS_STATES else "not_authorized",
            "notes": note,
            "secrets_stored": False,
        }
    )
    access.setdefault("source_state", "not_acquired")
    access.setdefault("handoff_state", "not_required")
    access.setdefault("verification", {})
    if browser_backend:
        access["browser_backend"] = browser_backend
    return access


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument(
        "--status",
        required=True,
        choices=STATUS_CHOICES,
    )
    parser.add_argument("--verified-by", choices=("browser", "manual"), default="browser")
    parser.add_argument("--browser-backend", choices=BACKENDS)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    if SECRET_VALUE_RE.search(args.note):
        parser.error("--note appears to contain an authentication secret")

    job_dir = args.job.expanduser().resolve()
    job_dir = args.job.expanduser().resolve()
    prior_path = job_dir / "metadata" / "access.json"
    prior = {}
    if prior_path.exists():
        import json

        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    access = build_access_record(
        args.status,
        args.verified_by,
        args.note,
        browser_backend=args.browser_backend,
        prior=prior,
    )
    if access["access_state"] in {"authenticated", "member_authorized"} and args.verified_by != "browser":
        parser.error(
            "authenticated/member_authorized access must be verified in a browser; "
            "manual status cannot establish entitlement"
        )
    write_json(job_dir / "metadata" / "access.json", access)
    state = canonical_access_state(access)
    if state in AUTHORIZED_ACCESS_STATES:
        update_pipeline_state(job_dir, "access", "complete", state)
    else:
        update_pipeline_state(job_dir, "access", "blocked", state)
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
