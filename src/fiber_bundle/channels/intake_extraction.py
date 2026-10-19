"""No-model derivation after original archival, with durable per-link attempts."""

import json
import mimetypes
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import articles, auth, database, objects
from ..config import Config, InvalidInput
from ..inputs import AssetInput, canonical
from ..store import Store
from . import intake_fetch, intake_links, store
from . import intake_repository as repository
from .intake_processing import guard_locked, record
from .services import policy as service_policy


def update(config: Config, identity: str, batch: str, index: int, changes: dict[str, Any]) -> None:
    with database.locked(config), store.opened(config) as side, database.transaction(side):
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (identity, batch),
        ).fetchone()
        value = json.loads(row["value"])
        if changes.get("state") != "cancelled":
            from ..intake_abort_store import guard as abort_guard

            abort_guard(config, {"channel": identity, "batch": batch, "index": index})
            if value.get("aborted") or value["links"][index].get("aborted"):
                raise InvalidInput("intake_user_aborted")
        value["links"][index].update(changes)
        value["extraction"] = (
            "cancelled"
            if value.get("aborted")
            else "pending"
            if any(link.get("state") in {None, "pending"} for link in value["links"])
            else "complete"
        )
        record(side, identity, batch, row["revision"], row["state"], row["reason"], value)


def guard(
    config: Config, identity: str, batch: str, revision: int, *, index: int | None = None
) -> dict[str, Any]:
    from ..intake_abort_store import guard as abort_guard

    abort_guard(config, {"channel": identity, "batch": batch, "index": index})
    with database.locked(config), store.readonly(config) as side:
        if side is None:
            raise InvalidInput("intake_policy_unavailable")
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (identity, batch),
        ).fetchone()
        if row is None or row["state"] != "stored":
            raise InvalidInput("intake_originals_required")
        value = json.loads(row["value"])
        policy = guard_locked(config, identity, revision, manual=bool(value.get("manual")))
        if (
            row["revision"] != revision
            or value.get("extraction") == "cancelled"
            or (index is not None and value["links"][index].get("aborted"))
        ):
            raise InvalidInput("intake_extraction_cancelled")
        return policy


def advance(config: Config, identity: str) -> bool:
    channel, policy = repository.channel(config, identity)
    if not policy["settings"]["fetch_articles"] and not policy["settings"]["fetch_subtitles"]:
        return False
    with database.locked(config), store.opened(config) as side:
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND revision=? "
            "AND state='stored' AND json_extract(value,'$.extraction')='pending' "
            "AND created>=? AND EXISTS(SELECT 1 "
            "FROM json_each(channel_intake_records.value,'$.links') l "
            "WHERE json_extract(l.value,'$.state') IS NULL "
            "OR json_extract(l.value,'$.state')='pending') "
            "ORDER BY created LIMIT 1",
            (identity, policy["revision"], service_policy.cutoff(config, identity)),
        ).fetchone()
        if row is None:
            return False
        value = json.loads(row["value"])
        guard_locked(config, identity, policy["revision"], manual=bool(value.get("manual")))
        index = next(
            (i for i, link in enumerate(value["links"]) if link.get("state") in {None, "pending"}),
            None,
        )
        if index is None:
            return False
        link = value["links"][index]
        if value.get("aborted") or link.get("aborted"):
            raise InvalidInput("intake_user_aborted")
        kind = intake_links.site(link["original_url"]) if link["allowed"] else None
        supported = (kind in {"x", "wechat"} and policy["settings"]["fetch_articles"]) or (
            kind in {"youtube", "bilibili", "wechat_channels"}
            and policy["settings"]["fetch_subtitles"]
        )
        attempt = link.pop("queued_attempt_id", None) or str(uuid4())
        link.update(
            state="extracting" if supported else "link_only",
            attempt_id=attempt,
            reason=None if supported else "intake_extractor_unavailable",
        )
        with database.transaction(side):
            if not supported:
                value["extraction"] = (
                    "pending"
                    if any(item.get("state") in {None, "pending"} for item in value["links"])
                    else "complete"
                )
            record(
                side, identity, row["batch_id"], row["revision"], row["state"], row["reason"], value
            )
    if not supported:
        return True
    batch = row["batch_id"]

    def checked() -> None:
        guard(config, identity, batch, row["revision"], index=index)

    def reserve(url: str) -> None:
        with database.locked(config), store.opened(config) as side, database.transaction(side):
            guard_locked(config, identity, row["revision"], manual=bool(value.get("manual")))
            if not repository.reserve(
                side,
                identity,
                policy["settings"],
                "download",
                attempt + ":" + objects.digest(url.encode()),
            ):
                raise InvalidInput("intake_download_budget_exhausted")

    phase = "capture"
    try:
        if link.get("resume_processing"):
            from .intake_pi import run as resume_processing

            phase = "pi"
            resume_processing(config, identity, batch, index)
            return True
        if kind in {"bilibili", "youtube"} and policy["settings"].get("policy_version") == 2:
            from .intake_video_skill import acquire as video_acquire

            result = video_acquire(config, identity, batch, index, checked)
        else:
            result = intake_fetch.acquire(
                link["original_url"], policy["settings"], reserve, checked
            )
        checked()
        with database.locked(config):
            guard_locked(config, identity, row["revision"], manual=bool(value.get("manual")))
            main = database.connect(config.state_root)
            try:
                vault = auth.grant(
                    main, channel["value"]["client_id"], value["target_vault_id"], "add"
                )
                receipt = Store(config).ingest_locked(
                    main,
                    ingress_scope="channel-intake-extraction:" + identity,
                    event_id=attempt,
                    vault=vault["name"],
                    source_key="intake-link:" + identity + ":" + batch + ":" + str(index),
                    raw_event=canonical(
                        {
                            "batch_id": batch,
                            "index": index,
                            "attempt_id": attempt,
                            "parent_receipts": value["receipts"],
                        }
                    ).encode(),
                    items=(
                        *result.assets,
                        *(
                            AssetInput(
                                data,
                                Path(name).name,
                                mimetypes.guess_type(name)[0] or "application/octet-stream",
                                "body" if name == "note.md" else "attachment",
                            )
                            for name, data in result.files.items()
                        ),
                    ),
                    session_scope="channel-intake",
                    session_id=batch,
                    retention="knowledge",
                    owner_client_id=channel["value"]["client_id"],
                )
                if receipt.state != "stored":
                    raise InvalidInput("intake_extraction_archive_incomplete")
            finally:
                main.close()
        update(
            config,
            identity,
            batch,
            index,
            {
                "state": result.metadata["content_state"],
                "metadata": result.metadata,
                "receipt": asdict(receipt),
                "reason": result.metadata.get("reason"),
                "files": {
                    name: {
                        "sha256": objects.digest(data),
                        "bytes": len(data),
                        "original_name": Path(name).name,
                    }
                    for name, data in result.files.items()
                },
            },
        )
        # The captured note is the extraction result. Optional summaries have a
        # separate job and must never hold this source in the extraction stage.
    except Exception as exc:
        if isinstance(exc, InvalidInput) and str(exc) in {
            "intake_user_aborted",
            "intake_extraction_cancelled",
        }:
            update(
                config,
                identity,
                batch,
                index,
                {"state": "cancelled", "reason": "intake_user_aborted"},
            )
            return True
        safe = (
            exc.code
            if isinstance(exc, articles.ArticleError)
            else (
                str(exc)
                if isinstance(exc, InvalidInput)
                else "intake_processing_failed"
                if phase == "pi"
                else "intake_fetch_failed"
            )
        )
        # Exception text from remote libraries is never surfaced. Only our finite codes.
        known = (
            safe if safe.startswith("intake_") and len(safe) < 100 else "intake_content_incomplete"
        )
        from ..model_configuration.errors import MESSAGES as model_errors
        from ..processing_errors import MESSAGES as processing_errors
        from .diagnostics import failure

        if safe in model_errors or safe in processing_errors:
            known = safe

        update(
            config,
            identity,
            batch,
            index,
            {
                "state": "needs_review",
                "reason": known,
                "diagnostic": failure(exc)["diagnostic"],
            },
        )
    return True


def retry(
    config: Config,
    identity: str,
    batch: str,
    index: int,
    revision: int,
    *,
    leased: bool = False,
    approve_new_model_attempt: bool = False,
    video_note_mode: str | None = None,
) -> dict[str, Any]:
    from . import operations

    with repository.lease(config, leased), store.opened(config) as side, database.transaction(side):
        channel = operations.entry(config, side, identity)
        policy = repository.policy(side, identity)
        if policy["revision"] != revision:
            raise InvalidInput("intake_revision_conflict")
        main = database.connect(config.state_root)
        try:
            repository.authorize(config, main, channel, policy["settings"])
        finally:
            main.close()
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (identity, batch),
        ).fetchone()
        if row is None or row["state"] != "stored":
            raise InvalidInput("intake_originals_required")
        value = json.loads(row["value"])
        if not 0 <= index < len(value["links"]):
            raise InvalidInput("intake_link_unavailable")
        link = value["links"][index]
        if value.get("aborted") or link.get("aborted"):
            raise InvalidInput("intake_user_aborted")
        if link.get("state") == "extracting":
            from .. import feishu_runtime
            from . import control, legacy

            runtime = (
                feishu_runtime.inspect(legacy.home(config, channel))
                if channel.get("origin") == "legacy"
                else control.status(config, identity)
            )
            if runtime["process_state"] != "stopped":
                raise InvalidInput("intake_stop_before_recovery")
        kind = intake_links.site(link["original_url"])
        if kind not in {"x", "wechat", "youtube", "bilibili", "wechat_channels"}:
            raise InvalidInput("intake_extractor_unavailable")
        setting = "fetch_articles" if kind in {"x", "wechat"} else "fetch_subtitles"
        if not policy["settings"][setting]:
            raise InvalidInput(
                "intake_article_extraction_disabled"
                if setting == "fetch_articles"
                else "intake_subtitle_extraction_disabled"
            )
        if link.get("publication"):
            raise InvalidInput("intake_published_retry_forbidden")
        if not intake_links.allowed(link["original_url"], policy["settings"]):
            raise InvalidInput("intake_site_not_allowed")
        if value["target_vault_id"] != policy["settings"]["target_vault_id"]:
            raise InvalidInput("intake_target_changed")
        if video_note_mode not in (None, "visual_evidence"):
            raise InvalidInput("video_skill_visual_audit_failed")
        if video_note_mode and (not link.get("video_processing") or link.get("pi_processing")):
            raise InvalidInput("video_skill_visual_audit_failed")
        from ..intake_abort_store import guard as abort_guard

        abort_guard(config, {"channel": identity, "batch": batch, "index": index})
        attempt = str(uuid4())
        link.pop("diagnostic", None)
        link.update(
            state="pending",
            reason=None,
            review=None,
            resume_processing=bool(link.get("pi_processing")),
            queued_attempt_id=attempt,
            attempt_id=attempt,
        )
        value.update(extraction="pending", manual=True)
        if "receipt_tracking" not in value:
            value["receipt_tracking"] = {"version": 2, "next_check": 0}
            if value.get("receipt_state"):
                value.setdefault("receipt_updates", {})["received"] = {
                    "stage": "received",
                    "state": value["receipt_state"],
                    "legacy": True,
                }
        value["receipt_tracking"].update(next_check=0, done=False)
        from ..automatic_intake import resume_capture

        with database.connect(config.state_root) as main:
            from .intake_pi import prepare_retry

            prepare_retry(main, link, approve_new_attempt=approve_new_model_attempt)
            if link.get("video_processing"):
                from .intake_video_skill import prepare_retry as retry_video

                retry_video(
                    main,
                    link,
                    approve_new_attempt=approve_new_model_attempt,
                    video_note_mode=video_note_mode,
                )
            resume_capture(config, main, identity, batch, index, attempt_id=attempt)
        record(side, identity, batch, revision, row["state"], row["reason"], value)
    return {"queued": True, "executed": False}
