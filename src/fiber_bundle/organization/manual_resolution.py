"""Independent completion receipts; immutable analysis plans and diagnostics stay intact."""

import time
from typing import Any

from . import store


def key(job: Any, entity: str) -> str:
    return str(job["id"]) + ":" + entity


def records(config: Any, job: Any) -> dict[str, Any]:
    if not all(job.get(k) for k in ("id", "client_id", "vault_id")):
        return {}
    rows = store.get_many(
        config,
        "manual_resolutions",
        [key(job, i["entity"]) for i in job["items"] if i.get("entity")],
    )
    return {
        row["entity"]: row
        for row in rows.values()
        if row.get("state") == "resolved"
        and row.get("method") == "manual_moc"
        and row["job_id"] == job["id"]
        and row["client_id"] == job["client_id"]
        and row["vault_id"] == job["vault_id"]
    }


def resolved(config: Any, job: Any) -> set[str]:
    return set(records(config, job))


def exception(item: Any) -> bool:
    return bool(item.get("analysis_error") or item.get("error") or item.get("status") == "review")


def record(config: Any, client: str, vault: str, proof: Any) -> int:
    values = {}
    for job in store.rows(config, "jobs", vault):
        if job["client_id"] != client:
            continue
        native_expert = proof["entity"].startswith("expert:") and not job.get("maintenance")
        if native_expert and job["state"] in {"completed", "cancelled"}:
            continue
        if not job.get("maintenance") and not native_expert:
            continue
        existing = records(config, job)
        for item in job["items"]:
            if item["entity"] != proof["entity"] or (not native_expert and not exception(item)):
                continue
            if item["sha256"] not in proof["source_versions"]:
                continue
            prior = existing.get(item["entity"])
            receipt = {
                **{k: v for k, v in proof.items() if k != "source_versions"},
                "job_id": job["id"],
                "client_id": client,
                "vault_id": vault,
                "state": "resolved",
                "method": "manual_moc",
                "resolved_at": prior["resolved_at"] if prior else time.time(),
            }
            if prior:
                receipt["moc_ids"] = sorted(set(prior["moc_ids"]) | set(receipt["moc_ids"]))
                receipt["moc_evidence"] = {**prior["moc_evidence"], **receipt["moc_evidence"]}
            if receipt != prior:
                values[key(job, item["entity"])] = receipt
    store.put_many(config, "manual_resolutions", vault, values)
    return len(values)


def project(config: Any, job: Any) -> Any:
    found = records(config, job)
    if not found:
        return job
    value = {
        **job,
        "items": [
            {
                **item,
                "manual_resolution": {
                    k: found[item["entity"]][k]
                    for k in ("state", "method", "resolved_at", "moc_ids", "path", "source_sha256")
                },
            }
            if item["entity"] in found
            else dict(item)
            for item in job["items"]
        ],
    }
    if (
        job["state"] != "cancelled"
        and not job.get("maintenance")
        and all(
            item["entity"] in found or item["state"] in {"done", "suggested"}
            for item in job["items"]
        )
    ):
        value.update(state="completed", completed=len(job["items"]))
    # Only an item-scoped historical diagnosis can disappear from active feedback.
    error = job.get("error")
    failed = [
        i for i in job["items"] if error and error in {i.get("error"), i.get("analysis_error")}
    ]
    if failed and all(i["entity"] in found for i in failed):
        value.update(
            error=None, historical_error={"error": error, "detail": job.get("error_detail")}
        )
    return value
