"""No-model derivation after original archival, with durable per-link attempts."""

import json
import mimetypes
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import article_fetch, articles, auth, database, objects
from ..config import Config, IntegrityError, InvalidInput
from ..inputs import AssetInput, canonical
from ..store import Store
from . import intake_fetch, intake_links, store
from . import intake_repository as repository
from .intake_article_checkpoint import ArticleCheckpoint
from .intake_article_recovery import capture_lease, describe, stored_receipt
from .intake_processing import guard_locked, record
from .services import policy as service_policy


def _pending(
    side: sqlite3.Connection, identity: str, revision: int, after: float, *, hint: bool = False
) -> sqlite3.Row | None:
    fields = "1" if hint else "*"
    row: sqlite3.Row | None = side.execute(
        f"SELECT {fields} FROM channel_intake_records WHERE channel_id=? AND revision=? "
        "AND state='stored' AND json_extract(value,'$.extraction')='pending' "
        "AND created>=? AND EXISTS(SELECT 1 "
        "FROM json_each(channel_intake_records.value,'$.links') l "
        "WHERE json_extract(l.value,'$.state') IS NULL "
        "OR json_extract(l.value,'$.state')='pending' "
        "OR (json_extract(l.value,'$.state')='extracting' "
        "AND json_extract(l.value,'$.article_capture_v1')=1)) "
        "ORDER BY created LIMIT 1",
        (identity, revision, after),
    ).fetchone()
    return row


def update(
    config: Config,
    identity: str,
    batch: str,
    index: int,
    changes: dict[str, Any],
    *,
    attempt: str | None = None,
) -> None:
    with database.locked(config), store.opened(config) as side, database.transaction(side):
        row = side.execute(
            "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
            (identity, batch),
        ).fetchone()
        value = json.loads(row["value"])
        link = value["links"][index]
        if attempt is not None:
            if changes.get("state") == "cancelled" and link.get("state") == "cancelled":
                return
            _owned(link, attempt)
        if changes.get("state") != "cancelled":
            from ..intake_abort_store import guard as abort_guard

            abort_guard(config, {"channel": identity, "batch": batch, "index": index})
            if value.get("aborted") or value["links"][index].get("aborted"):
                raise InvalidInput("intake_user_aborted")
        link.update(changes)
        value["extraction"] = (
            "cancelled"
            if value.get("aborted")
            else "pending"
            if any(_claimable(link) for link in value["links"])
            else "complete"
        )
        record(side, identity, batch, row["revision"], row["state"], row["reason"], value)


def _claimable(link: dict[str, Any]) -> bool:
    return link.get("state") in {None, "pending"} or (
        link.get("state") == "extracting" and link.get("article_capture_v1") is True
    )


def _owned(link: dict[str, Any], attempt: str) -> None:
    if link.get("attempt_id") != attempt or link.get("state") != "extracting":
        raise InvalidInput("intake_capture_attempt_changed")


def guard(
    config: Config,
    identity: str,
    batch: str,
    revision: int,
    *,
    index: int | None = None,
    attempt: str | None = None,
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
        if index is not None and attempt is not None:
            _owned(value["links"][index], attempt)
        return policy


def advance(config: Config, identity: str) -> bool:
    channel, policy = repository.channel(config, identity)
    if not policy["settings"]["fetch_articles"] and not policy["settings"]["fetch_subtitles"]:
        return False
    with store.readonly(config) as side:
        if side is None or (
            store.schema_ready(side)
            and _pending(
                side,
                identity,
                policy["revision"],
                service_policy.cutoff(config, identity),
                hint=True,
            )
            is None
        ):
            return False
    with capture_lease(config, identity) as acquired:
        return _advance(config, identity, channel, policy) if acquired else False


def _advance(
    config: Config, identity: str, channel: dict[str, Any], policy: dict[str, Any]
) -> bool:
    with database.locked(config), store.opened(config) as side:
        service = service_policy.get(config, identity, "intake")
        if service and not service["enabled"]:
            return False
        row = _pending(side, identity, policy["revision"], service_policy.cutoff(config, identity))
        if row is None:
            return False
        value = json.loads(row["value"])
        guard_locked(config, identity, policy["revision"], manual=bool(value.get("manual")))
        index = next((i for i, link in enumerate(value["links"]) if _claimable(link)), None)
        if index is None:
            return False
        link = value["links"][index]
        if value.get("aborted") or link.get("aborted"):
            raise InvalidInput("intake_user_aborted")
        kind = intake_links.site(link["original_url"]) if link["allowed"] else None
        supported = (
            kind in {"x", "wechat", "fibermall"} and policy["settings"]["fetch_articles"]
        ) or (
            kind in {"youtube", "bilibili", "wechat_channels"}
            and policy["settings"]["fetch_subtitles"]
        )
        wechat = kind == "wechat" and supported
        interrupted = wechat and link.get("state") == "extracting"
        attempt = (
            link["attempt_id"]
            if interrupted
            else link.pop("queued_attempt_id", None) or str(uuid4())
        )
        link.update(
            state="extracting" if supported else "link_only",
            attempt_id=attempt,
            reason=None if supported else "intake_extractor_unavailable",
        )
        if wechat:
            link["article_capture_v1"] = True
        with database.transaction(side):
            if not supported:
                value["extraction"] = (
                    "pending" if any(_claimable(item) for item in value["links"]) else "complete"
                )
            record(
                side, identity, row["batch_id"], row["revision"], row["state"], row["reason"], value
            )
    if not supported:
        return True
    batch = row["batch_id"]
    owner = attempt if wechat else None
    prepared = link.get("article_capture_prepared") if wechat else None
    progress: dict[str, Any] | None = link.get("article_image_progress") if wechat else None
    next_progress = 0.0

    def checked() -> None:
        guard(config, identity, batch, row["revision"], index=index, attempt=owner)

    def reserve(url: str) -> None:
        with database.locked(config), store.opened(config) as side, database.transaction(side):
            guard_locked(config, identity, row["revision"], manual=bool(value.get("manual")))
            target = attempt + ":" + objects.digest(url.encode())
            if (
                wechat
                and side.execute(
                    "SELECT 1 FROM channel_intake_consumption "
                    "WHERE channel_id=? AND kind='download' AND target=?",
                    (identity, target),
                ).fetchone()
            ):
                return  # The same durable capture already reserved this resource.
            if not repository.reserve(
                side,
                identity,
                policy["settings"],
                "download",
                target,
            ):
                raise InvalidInput("intake_download_budget_exhausted")

    def report(done: int, total: int) -> None:
        nonlocal progress, next_progress
        progress = {"state": "downloading", "complete": done, "total": total}
        now = time.monotonic()
        if now >= next_progress:
            update(
                config, identity, batch, index, {"article_image_progress": progress}, attempt=owner
            )
            next_progress = now + 1

    phase = "capture"
    try:
        if link.get("resume_processing"):
            from .intake_pi import run as resume_processing

            phase = "pi"
            resume_processing(config, identity, batch, index)
            return True
        scope = "channel-intake-extraction:" + identity
        source_key = "intake-link:" + identity + ":" + batch + ":" + str(index)
        client_id = channel["value"]["client_id"]
        raw_event = canonical(
            {
                "batch_id": batch,
                "index": index,
                "attempt_id": attempt,
                "parent_receipts": value["receipts"],
            }
        ).encode()
        checkpoint = (
            ArticleCheckpoint(
                config.state_root,
                identity=identity,
                batch=batch,
                index=index,
                revision=row["revision"],
                target_vault_id=value["target_vault_id"],
                source=link["original_url"],
            )
            if wechat
            else None
        )
        receipt = None
        if wechat and prepared is not None:
            checked()
            with database.locked(config), database.connect(config.state_root) as main:
                vault = auth.grant(main, client_id, value["target_vault_id"], "add")
                receipt = stored_receipt(
                    config,
                    main,
                    scope,
                    attempt,
                    client_id=client_id,
                    vault=vault["name"],
                    source_key=source_key,
                    batch=batch,
                    raw_event=raw_event,
                    prepared=prepared,
                )
        if receipt is not None:
            assert prepared is not None
            result = None
        elif checkpoint is not None:
            result = intake_fetch.acquire(
                link["original_url"],
                policy["settings"],
                reserve,
                checked,
                checkpoint=checkpoint,
                progress=report,
            )
        elif kind in {"bilibili", "youtube"} and policy["settings"].get("policy_version") == 2:
            from .intake_video_skill import acquire as video_acquire

            result = video_acquire(config, identity, batch, index, checked)
        else:
            result = intake_fetch.acquire(
                link["original_url"], policy["settings"], reserve, checked
            )
        checked()
        if result is not None:
            captured = {"metadata": result.metadata, "files": describe(result.files)}
            if prepared is not None and prepared != captured:
                raise IntegrityError("intake capture receipt content changed")
            prepared = captured
            if wechat:
                update(
                    config,
                    identity,
                    batch,
                    index,
                    {"article_capture_prepared": prepared},
                    attempt=owner,
                )
            phase = "archive"
            with database.locked(config), database.connect(config.state_root) as main:
                guard_locked(config, identity, row["revision"], manual=bool(value.get("manual")))
                from ..intake_abort_store import guard as abort_guard

                abort_guard(config, {"channel": identity, "batch": batch, "index": index})
                vault = auth.grant(main, client_id, value["target_vault_id"], "add")
                receipt = Store(config).ingest_locked(
                    main,
                    ingress_scope=scope,
                    event_id=attempt,
                    vault=vault["name"],
                    source_key=source_key,
                    raw_event=raw_event,
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
                    owner_client_id=client_id,
                )
                if receipt.state != "stored":
                    raise InvalidInput("intake_extraction_archive_incomplete")
        assert prepared is not None and receipt is not None
        metadata = prepared["metadata"]
        image_count = metadata.get("image_count", 0)
        update(
            config,
            identity,
            batch,
            index,
            {
                "state": metadata["content_state"],
                "metadata": metadata,
                "receipt": asdict(receipt),
                "reason": metadata.get("reason"),
                "files": prepared["files"],
                **(
                    {
                        "article_capture_prepared": None,
                        "article_image_progress": {
                            "state": "complete",
                            "complete": image_count,
                            "total": image_count,
                        },
                    }
                    if wechat
                    else {}
                ),
            },
            attempt=owner,
        )
        if checkpoint is not None:
            if result is None:
                intake_fetch.checkpoint_io(checkpoint.load_source)
            intake_fetch.checkpoint_io(checkpoint.clear)
        # The captured note is the extraction result. Optional summaries have a
        # separate job and must never hold this source in the extraction stage.
    except Exception as exc:
        if wechat and (
            isinstance(exc, database.LockTimeout)
            or isinstance(exc, InvalidInput)
            and str(exc) == "intake_capture_attempt_changed"
        ):
            raise
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
                attempt=owner,
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
                else "intake_extraction_archive_incomplete"
                if phase == "archive" or isinstance(exc, IntegrityError) and wechat
                else "intake_fetch_failed"
            )
        )
        # Exception text from remote libraries is never surfaced. Only our finite codes.
        known = (
            safe if safe.startswith("intake_") and len(safe) < 100 else "intake_content_incomplete"
        )
        from ..model_configuration.errors import MESSAGES as model_errors
        from ..processing_errors import MESSAGES as processing_errors
        from ..video_source_transport import SourceConnectionError
        from .diagnostics import failure

        if safe in model_errors or safe in processing_errors:
            known = safe

        image_failure = exc if isinstance(exc, article_fetch.ImageFetchError) else None
        if prepared is not None and progress is not None:
            image_count = prepared["metadata"].get("image_count", 0)
            progress = {"state": "complete", "complete": image_count, "total": image_count}
        elif progress is not None and phase == "capture":
            progress = {
                **progress,
                "state": "pending",
                "failure_code": known,
                "can_resume": (
                    image_failure.retryable
                    if image_failure is not None
                    else known == "intake_fetch_timeout"
                ),
            }
        update(
            config,
            identity,
            batch,
            index,
            {
                "state": "needs_review",
                "reason": known,
                "diagnostic": {
                    **failure(exc)["diagnostic"],
                    **(exc.diagnostic if isinstance(exc, SourceConnectionError) else {}),
                    **(
                        {
                            **image_failure.diagnostic,
                            "image_ordinal": progress["complete"] + 1,
                            "image_count": progress["total"],
                        }
                        if image_failure is not None and progress is not None
                        else {}
                    ),
                },
                **({"article_image_progress": progress} if progress is not None else {}),
            },
            attempt=owner,
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
    use_current_video_settings: bool = False,
    max_new_model_calls: int | None = None,
    max_video_tokens: int | None = None,
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
        if max_video_tokens is not None:
            from ..video_token_budget import validate_limit

            validate_limit(max_video_tokens)
            if not link.get("video_processing") or max_new_model_calls is not None:
                raise InvalidInput("video_skill_token_budget_invalid")
            if link.get("state") in {"pending", "extracting"}:
                raise InvalidInput("video_skill_recovery_already_queued")
        if max_new_model_calls is not None:
            from ..video_call_allowance import new

            if not link.get("video_processing"):
                raise InvalidInput("video_skill_call_allowance_invalid")
            new(max_new_model_calls)  # Validate direct callers before any record changes.
            if link.get("state") in {"pending", "extracting"}:
                raise InvalidInput("video_skill_recovery_already_queued")
        if value.get("aborted") or link.get("aborted"):
            raise InvalidInput("intake_user_aborted")
        kind = intake_links.site(link["original_url"])
        wechat = kind == "wechat"
        recovering_capture = wechat and (
            link.get("article_capture_prepared") is not None
            or link.get("article_capture_v1") is True
            and link.get("state") == "extracting"
        )
        if wechat and link.get("state") == "pending":
            raise InvalidInput("intake_article_capture_already_queued")
        if recovering_capture and row["revision"] != revision:
            raise InvalidInput("intake_revision_conflict")
        if link.get("state") == "extracting":
            if recovering_capture:
                with capture_lease(config, identity) as available:
                    if not available:
                        raise InvalidInput("intake_article_capture_already_queued")
            else:
                from .. import feishu_runtime
                from . import control, legacy

                runtime = (
                    feishu_runtime.inspect(legacy.home(config, channel))
                    if channel.get("origin") == "legacy"
                    else control.status(config, identity)
                )
                if runtime["process_state"] != "stopped":
                    raise InvalidInput("intake_stop_before_recovery")
        if kind not in {"x", "wechat", "fibermall", "youtube", "bilibili", "wechat_channels"}:
            raise InvalidInput("intake_extractor_unavailable")
        setting = "fetch_articles" if kind in {"x", "wechat", "fibermall"} else "fetch_subtitles"
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
        if video_note_mode not in (None, "visual_evidence", "captured_evidence", "direct"):
            raise InvalidInput("video_skill_visual_audit_failed")
        if video_note_mode and (not link.get("video_processing") or link.get("pi_processing")):
            raise InvalidInput("video_skill_visual_audit_failed")
        from ..intake_abort_store import guard as abort_guard

        abort_guard(config, {"channel": identity, "batch": batch, "index": index})
        if link.get("video_processing") or (wechat and link.get("diagnostic")):
            link.setdefault("retry_history", []).append(
                {
                    "attempt_id": link.get("attempt_id"),
                    "reason": link.get("reason"),
                    "diagnostic": link.get("diagnostic"),
                }
            )
        if use_current_video_settings:
            from .. import video_token_budget
            from ..video_recovery import refresh

            previous_budget = (
                video_token_budget.descriptor(link["video_processing"])
                if link.get("video_processing")
                else None
            )
            if previous_budget is not None and max_video_tokens is not None:
                previous_budget["limit"] = max_video_tokens
            refresh(config, link)
            if previous_budget is not None:
                link["pending_video_token_budget"] = previous_budget
            if max_new_model_calls is not None:
                from ..video_call_allowance import new

                link["pending_video_call_allowance"] = new(max_new_model_calls)
        attempt = link["attempt_id"] if recovering_capture else str(uuid4())
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
                    max_new_model_calls=max_new_model_calls,
                    max_video_tokens=max_video_tokens,
                )
            resume_capture(config, main, identity, batch, index, attempt_id=attempt)
        record(side, identity, batch, revision, row["state"], row["reason"], value)
    return {"queued": True, "executed": False}
