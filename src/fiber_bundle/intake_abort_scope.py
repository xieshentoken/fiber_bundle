"""Resolve a task number only within its authenticated Feishu owner's channel."""

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import database, weixin_batches, weixin_inbox
from .channels import batch_intake, store
from .config import Config
from .weixin_events import Binding


def job_context(job: dict[str, Any]) -> dict[str, Any]:
    value = job["value"]
    return dict(
        value.get("intake") or value.get("channel_batch") or value.get("summary_source") or {}
    )


def committed(db: Any, scope: dict[str, Any], value: dict[str, Any] | None = None) -> bool:
    if scope["kind"] == "link":
        if (
            value
            and (value["links"][scope["index"]].get("publication") or {}).get("state")
            == "published"
        ):
            return True
        kind, identity = (
            "intake",
            str(
                uuid5(
                    NAMESPACE_URL,
                    f"fiber-intake:{scope['channel']}:{scope['batch']}:{scope['index']}",
                )
            ),
        )
    elif scope["kind"] == "job":
        row = db.execute("SELECT * FROM automatic_jobs WHERE id=?", (scope["job_id"],)).fetchone()
        if row is None:
            return False
        payload = json.loads(row["value"])
        if row["phase"] == "index" or payload.get("publication"):
            return True
        if row["kind"] == "intake_summary":
            kind, identity = "intake", row["id"]
        elif payload.get("chat_archive"):
            # The chat publisher records its stable publication identity below.
            kind, identity = "intake", row["source_id"]
        else:
            kind, identity = row["kind"], payload.get("run_id", row["source_id"])
    else:
        return False
    row = db.execute(
        "SELECT state,entry_note_path,value FROM publication_locations WHERE "
        "kind=? AND publication_id=?",
        (kind, identity),
    ).fetchone()
    return bool(
        row
        and (
            row["state"] == "published"
            or row["entry_note_path"] in json.loads(row["value"]).get("claims", {})
        )
    )


def resolve(config: Config, binding: Binding, target: str | None) -> dict[str, Any]:
    entry = batch_intake.channel_for(config, binding)
    if entry is None:
        return {"state": "not_found"}
    channel = entry["id"]
    with store.readonly(config) as side:
        records = (
            [
                dict(r)
                for r in side.execute(
                    "SELECT * FROM channel_intake_records AS records "
                    "WHERE channel_id=? AND (?='' OR batch_id LIKE ? OR "
                    "EXISTS(SELECT 1 FROM json_each(records.value,'$.links') AS link "
                    "WHERE json_extract(link.value,'$.video_processing.job_id') LIKE ?)) "
                    "ORDER BY created DESC LIMIT 1001",
                    (channel, target or "", (target or "") + "%", (target or "") + "%"),
                )
            ]
            if side is not None
            else []
        )
    values = {r["batch_id"]: json.loads(r["value"]) for r in records}
    with database.connect(config.state_root) as db:
        jobs = [
            {**dict(r), "value": json.loads(r["value"])}
            for r in db.execute(
                "SELECT * FROM automatic_jobs WHERE client_id=? AND (?='' OR id LIKE ?) "
                "AND (json_extract(value,'$.intake.channel')=? OR "
                "json_extract(value,'$.channel_batch.channel')=? OR "
                "json_extract(value,'$.summary_source.channel')=?) "
                "AND (?!='' OR state IN ('queued','claimed','waiting','reviewed')) "
                "ORDER BY updated_at DESC LIMIT 1001",
                (
                    binding.client_id,
                    target or "",
                    (target or "") + "%",
                    channel,
                    channel,
                    channel,
                    target or "",
                ),
            )
        ]
        jobs = [j for j in jobs if job_context(j).get("channel") == channel]
        if target:
            scopes = []
            for batch, value in values.items():
                if batch.startswith(target):
                    scopes.append({"kind": "batch", "channel": channel, "batch": batch})
                for index, link in enumerate(value.get("links", [])):
                    if link.get("video_processing", {}).get("job_id", "").startswith(target):
                        scopes.append(
                            {"kind": "link", "channel": channel, "batch": batch, "index": index}
                        )
            for job in jobs:
                if job["id"].startswith(target):
                    context = job_context(job)
                    scopes.append(
                        {
                            "kind": "link" if job["kind"] == "intake" else "job",
                            **context,
                            "job_id": job["id"],
                            "channel": channel,
                        }
                    )
            if not scopes:
                with weixin_inbox.connection(config, binding) as journal:
                    scopes = [
                        {"kind": "batch", "channel": channel, "batch": r[0]}
                        for r in journal.execute(
                            "SELECT id FROM wx_batches WHERE account_id=? AND id LIKE ? LIMIT 2",
                            (binding.account_id, target + "%"),
                        )
                    ]
        else:
            active = {
                batch
                for batch, value in values.items()
                if not value.get("aborted")
                and any(link.get("state") == "extracting" for link in value.get("links", []))
            }
            for job in jobs:
                if job["state"] in {"claimed", "waiting"} and job["phase"] != "index":
                    context = job_context(job)
                    if context.get("batch") and not committed(
                        db, {"kind": "job", "job_id": job["id"]}
                    ):
                        active.add(context["batch"])
            if not active:
                for job in jobs:
                    if job["state"] in {"queued", "reviewed"} and job["phase"] != "index":
                        context = job_context(job)
                        if context.get("batch") and not committed(
                            db, {"kind": "job", "job_id": job["id"]}
                        ):
                            active.add(context["batch"])
            if not active:
                active = {
                    r["batch_id"]
                    for r in records
                    if not values[r["batch_id"]].get("aborted")
                    and (
                        r["state"] in {"waiting", "queued", "archiving"}
                        or any(
                            link.get("state") in {None, "pending"}
                            for link in values[r["batch_id"]].get("links", [])
                        )
                    )
                }
            if not active:
                weixin_batches.advance(config, binding)
                with weixin_inbox.connection(config, binding) as journal:
                    active = {
                        r[0]
                        for r in journal.execute(
                            "SELECT id FROM wx_batches WHERE account_id=? "
                            "AND state IN ('buffering','frozen') ORDER BY rowid DESC LIMIT 3",
                            (binding.account_id,),
                        )
                    }
            scopes = [
                {"kind": "batch", "channel": channel, "batch": batch} for batch in sorted(active)
            ]
        if len(scopes) != 1:
            return {
                "state": "ambiguous" if scopes else "not_found",
                "candidates": [s.get("job_id") or s["batch"] for s in scopes[:5]],
            }
        scope = scopes[0]
        if scope["kind"] != "batch" and committed(db, scope, values.get(scope.get("batch"))):
            return {"state": "too_late", "scope": scope}
        if scope["kind"] == "batch":
            value = values.get(scope["batch"], {})
            related = [j for j in jobs if job_context(j).get("batch") == scope["batch"]]
            if (
                value.get("links")
                and all(
                    (link.get("publication") or {}).get("state") == "published"
                    for link in value["links"]
                )
                and all(committed(db, {"kind": "job", "job_id": j["id"]}) for j in related)
            ):
                return {"state": "too_late", "scope": scope}
            if (
                related
                and not value.get("links")
                and all(committed(db, {"kind": "job", "job_id": j["id"]}) for j in related)
            ):
                return {"state": "too_late", "scope": scope}
        return {"state": "requested", "scope": scope}
