"""Explicit owner-scoped metadata preflight; no task creation, import or follow-up fetch."""

from typing import Any
from uuid import uuid4

from . import auth, media_jobs, media_link_worker, vaults
from .config import Config, InvalidInput
from .media_link_contract import source_url
from .media_link_failure import AcquisitionBlocked
from .media_worker import MediaPolicy


def run(config: Config, cid: str, vault: str, url: str, mode: str, image: str) -> dict[str, Any]:
    url = source_url(url)
    MediaPolicy(image).validate()
    if mode not in ("audio_only", "full_evidence"):
        raise InvalidInput("media_link_preflight_mode")
    identity = str(uuid4())
    spec = {"kind": "metadata_preflight", "url": url, "mode": mode}

    def guard() -> None:
        with media_jobs.connection(config) as db:
            vaults.checked_vault(config, auth.grant(db, cid, vault, "add"))

    guard()
    try:
        result = media_link_worker.execute_preflight(config, identity, image, spec, guard=guard)
        guard()
        return {"preflight_id": identity, "state": "inspected", **result}
    except AcquisitionBlocked as error:
        guard()
        return {
            "preflight_id": identity,
            "state": "blocked",
            "failure_reason": error.reason,
            "eligible_by_metadata": False,
            "publication_ready": False,
            "login_verified": False,
        }
