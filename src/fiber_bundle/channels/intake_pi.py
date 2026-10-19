"""Resumable Pi authoring after durable capture, retaining original note and image bytes."""

import mimetypes
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import automatic_policy, database, execution, execution_ledger, objects, pi_processing
from ..config import Config, InvalidInput
from ..inputs import AssetInput, canonical
from ..store import Store
from . import intake_links, intake_reviews, store
from . import intake_repository as repository
from .intake_extraction import guard, update
from .intake_processing import guard_locked


def run(config: Config, identity: str, batch: str, index: int) -> bool:
    channel, policy = repository.channel(config, identity)
    with (
        database.locked(config),
        database.connect(config.state_root) as db,
        store.readonly(config) as side,
    ):
        settings = automatic_policy.get(db, policy["settings"]["target_vault_id"])["settings"]
        if (
            settings.get("processing_mode") != "pi_skill"
            or settings.get("client_id") != channel["value"]["client_id"]
        ):
            return False
        if not automatic_policy.active(settings):
            raise InvalidInput("intake_processing_policy_disabled")
        _, row, value, files = intake_reviews.load(config, db, side, identity, batch, index)
        link = value["links"][index]
        if (link.get("publication") or {}).get("state") == "published":
            return True
        model = automatic_policy.processing_route(config, settings)
        frozen = link.get("pi_processing") or {
            "configuration_sha256": model.digest,
            "runs": {},
            "source_version_id": link["receipt"]["source_version_id"],
            "source_sha256": objects.digest(files["note.md"]),
        }
        if frozen["configuration_sha256"] != model.digest:
            raise InvalidInput("intake_processing_configuration_changed")
        legacy_complete = frozen.get("state") == "complete" and frozen.get("analysis_contract") != 2
        reuse_complete = frozen.get("state") == "complete" and not legacy_complete
        previous_gaps = list(link.get("processing_gaps") or link.get("processing_cautions", []))
        resume_legacy = legacy_complete or "resume_analysis_cautions" in frozen
        if resume_legacy:
            previous_gaps = list(frozen.setdefault("resume_analysis_cautions", previous_gaps))
        original = files["note.md"].decode()
        # Byte-bounded chunks preserve line order; each completed chunk is retained.
        chunks = text_chunks(original, min(model.max_request_bytes // 4, 16000))
        if not chunks or len(chunks) > 1000:
            raise InvalidInput("intake_processing_source_capacity")
    if reuse_complete:
        if not link.get("publication"):
            update(
                config,
                identity,
                batch,
                index,
                {
                    "pi_processing": frozen,
                    "state": link["metadata"]["content_state"],
                    "reason": link["metadata"].get("reason"),
                },
            )
            from ..automatic_intake import resume_analysis

            with database.locked(config), database.connect(config.state_root) as db:
                resume_analysis(config, db, identity, batch, index, previous_gaps)
        return True
    kind = "article" if intake_links.site(link["original_url"]) in {"x", "wechat"} else "video"
    # Freeze both the archive identity and old cautions before changing the durable state.
    frozen.setdefault(
        "archive_event_id", link["attempt_id"] + (":analysis-v2" if resume_legacy else "")
    )
    frozen["state"] = "processing"
    update(config, identity, batch, index, {"pi_processing": frozen, "state": "extracting"})
    execution_model = pi_processing.execution_policy(model)
    summaries = []
    for number, text in enumerate(chunks):
        guard(config, identity, batch, row["revision"], index=index)
        with database.locked(config), database.connect(config.state_root) as db:
            settings_now = automatic_policy.get(db, value["target_vault_id"])["settings"]
            if settings_now != settings:
                raise InvalidInput("intake_processing_configuration_changed")
            rid = frozen["runs"].get(str(number))
            if rid is None:
                rid = pi_processing.prepare(
                    config,
                    db,
                    execution_model,
                    channel["value"]["client_id"],
                    value["target_vault_id"],
                    frozen["source_version_id"],
                    kind,
                    frozen["source_sha256"],
                    [{"id": f"capture:{number}", "text": text}],
                    allow_cloud=settings["allow_model_source_content"],
                )
                frozen["runs"][str(number)] = rid
            current_run = execution_ledger.get_run(db, rid)
            state = current_run["state"]
            # Previously queued runs retain their exact frozen transport policy.
            run_model = model if current_run["config_digest"] == model.digest else execution_model
        update(config, identity, batch, index, {"pi_processing": frozen})
        if state in {"queued", "waiting_budget"}:
            execution.execute(config, run_model, rid, "fixed processing skill")
        with database.locked(config), database.connect(config.state_root) as db:
            run = execution_ledger.get_run(db, rid)
            state = run["state"]
            if state != "succeeded":
                from ..model_configuration.errors import MESSAGES as model_errors
                from ..processing_errors import MESSAGES as processing_errors

                error = run["error_code"]
                frozen["state"] = state
                update_fields = {
                    "pi_processing": frozen,
                    "state": "needs_review",
                    "reason": "intake_processing_budget_required"
                    if state == "waiting_budget"
                    else error
                    if error in model_errors or error in processing_errors
                    else "intake_processing_requires_reconciliation",
                }
            else:
                draft = pi_processing.result(config, db, rid)
                summaries.append(draft)
                update_fields = None
        if update_fields:
            update(config, identity, batch, index, update_fields)
            return True
    guard(config, identity, batch, row["revision"], index=index)
    frozen["analysis_contract"] = 2
    files["analysis.md"] = (
        "# Pi + Skill analysis\n\nknowledge_verified: false\n\n"
        "本分析基于分段文字，未核验图片内容；原文及已保存图片见原文笔记。\n\n"
        + "\n\n".join(
            f"## Part {n + 1}\n\n"
            + draft["summary"]
            + (
                "\n\n### 分析局限（不代表原件缺失）\n\n"
                + "\n".join("- " + gap.replace("\n", " ") for gap in draft["gaps"])
                if draft["gaps"]
                else ""
            )
            for n, draft in enumerate(summaries)
        )
    ).encode()
    files["analysis-provenance.json"] = canonical(
        {
            "schema": "fiber-intake-analysis/v1",
            "source_sha256": frozen["source_sha256"],
            "runs": summaries,
            "knowledge_verified": False,
        }
    ).encode()
    from ..publication_note_roles import analysis_manifest

    files["publication-notes.json"] = analysis_manifest()
    with database.locked(config), database.connect(config.state_root) as db:
        guard_locked(config, identity, row["revision"], manual=bool(value.get("manual")))
        vault = db.execute(
            "SELECT name FROM vaults WHERE id=?", (value["target_vault_id"],)
        ).fetchone()
        receipt = Store(config).ingest_locked(
            db,
            ingress_scope="channel-intake-analysis:" + identity,
            event_id=frozen["archive_event_id"],
            vault=vault["name"],
            source_key="intake-analysis:" + batch + ":" + str(index),
            raw_event=canonical(frozen).encode(),
            items=tuple(
                AssetInput(
                    data,
                    Path(name).name,
                    mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "body" if name == "note.md" else "attachment",
                )
                for name, data in files.items()
            ),
            session_scope="channel-intake",
            session_id=batch,
            retention="knowledge",
            owner_client_id=channel["value"]["client_id"],
        )
        if receipt.state != "stored":
            raise InvalidInput("intake_analysis_archive_incomplete")
    frozen["state"] = "complete"
    update(
        config,
        identity,
        batch,
        index,
        {
            "pi_processing": frozen,
            "state": link["metadata"]["content_state"],
            "reason": link["metadata"].get("reason"),
            "receipt": asdict(receipt),
            "files": {
                name: {
                    "sha256": objects.digest(data),
                    "bytes": len(data),
                    "original_name": Path(name).name,
                }
                for name, data in files.items()
            },
            "processing_gaps": [],
            "processing_cautions": sorted({gap for draft in summaries for gap in draft["gaps"]}),
        },
    )
    if resume_legacy:
        from ..automatic_intake import resume_analysis

        with database.locked(config), database.connect(config.state_root) as db:
            resume_analysis(config, db, identity, batch, index, previous_gaps)
    return True


def text_chunks(text: str, limit: int) -> list[str]:
    raw = text.encode()
    result = []
    start = 0
    while start < len(raw):
        end = min(len(raw), start + limit)
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        if end <= start:
            raise InvalidInput("intake_processing_chunk_capacity")
        result.append(raw[start:end].decode())
        start = end
    return result


def prepare_retry(db: Any, link: dict[str, Any], *, approve_new_attempt: bool = False) -> None:
    """Explicit replacement of failed chunks; never erase prior calls or their reservations."""
    frozen = link.get("pi_processing")
    if not frozen:
        return
    for number, rid in list(frozen["runs"].items()):
        run = execution_ledger.get_run(db, rid)
        if run["state"] == "running":
            raise InvalidInput("intake_processing_requires_reconciliation")
        if run["state"] not in {"failed", "interrupted", "cancelled", "waiting_budget"} and (
            rid not in frozen.get("rejected_runs", [])
        ):
            continue
        called = db.execute("SELECT 1 FROM model_calls WHERE run_id=?", (rid,)).fetchone()
        if run["state"] == "waiting_budget" and not called:
            continue
        if called and not approve_new_attempt:
            raise InvalidInput("intake_processing_new_attempt_confirmation_required")
        history = frozen.setdefault("previous_runs", [])
        if len(history) >= 1000:
            raise InvalidInput("intake_processing_source_capacity")
        history.append(
            {
                "chunk": number,
                "run_id": rid,
                "state": run["state"],
                "error_code": run["error_code"],
                "new_attempt_approved": approve_new_attempt,
            }
        )
        del frozen["runs"][number]
    frozen["state"] = (
        "complete"
        if frozen["runs"]
        and all(
            execution_ledger.get_run(db, rid)["state"] == "succeeded"
            for rid in frozen["runs"].values()
        )
        else "queued"
    )
