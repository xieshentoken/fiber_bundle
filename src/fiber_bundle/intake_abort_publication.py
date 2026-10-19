"""Cancellation checks for source jobs and the existing exclusive file publisher."""

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import intake_abort_scope, intake_abort_store
from .config import Config


def guard_job(config: Config, job: dict[str, Any]) -> None:
    if job["phase"] == "index" or job["value"].get("publication"):
        return
    context = intake_abort_scope.job_context(job)
    if not context:
        from .channels import intake_repository, store

        with store.readonly(config) as side:
            if side is not None and intake_repository.existing(side):
                matches = side.execute(
                    "SELECT channel_id,batch_id FROM channel_intake_records AS records "
                    "WHERE EXISTS(SELECT 1 FROM json_each(records.value,'$.automatic_imports') "
                    "AS item "
                    "WHERE json_extract(item.value,'$.job_id')=?) LIMIT 2",
                    (job["id"],),
                ).fetchall()
                if len(matches) == 1:
                    context = {"channel": matches[0][0], "batch": matches[0][1]}
    if context:
        intake_abort_store.guard(config, {**context, "job_id": job["id"]})


def guard_publication(config: Config, db: Any, row: dict[str, Any]) -> None:
    if row["state"] == "published":
        return
    source = row["plan"].get("intake_source", {})
    if source.get("channel_id"):
        intake_abort_store.guard(
            config,
            {
                "channel": source["channel_id"],
                "batch": source["batch_id"],
                "index": source["link_index"],
            },
        )
    matched = False
    for (identity,) in db.execute(
        "SELECT id FROM automatic_jobs WHERE "
        "id=? OR json_extract(value,'$.run_id')=? OR source_id=?",
        (row["publication_id"], row["publication_id"], row["publication_id"]),
    ):
        from .automatic_jobs import get

        guard_job(config, get(db, identity))
        matched = True
    # Legacy intake intents did not record a channel in their layout plan.
    if row["kind"] == "intake" and not source.get("channel_id") and not matched:
        from .automatic_jobs import get

        for (identity,) in db.execute("SELECT id FROM automatic_jobs WHERE kind='intake'"):
            job = get(db, identity)
            context = job["value"]["intake"]
            expected = str(
                uuid5(
                    NAMESPACE_URL,
                    f"fiber-intake:{context['channel']}:{context['batch']}:{context['index']}",
                )
            )
            if expected == row["publication_id"]:
                guard_job(config, job)
