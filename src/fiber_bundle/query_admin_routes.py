"""Host-approved policy management and finite asynchronous model jobs."""

from typing import Any

from fastapi import APIRouter, Request

from . import (
    cloud_answer_selection,
    cloud_answer_tasks,
    local_query_ledger,
    local_query_tasks,
    query_job_control,
    query_job_launch,
    query_jobs,
    query_policy_store,
    query_task_store,
)
from .answer_tasks import ScopedNoteAnswerInput
from .backend_lifecycle import BackendControl
from .config import Config
from .payloads import Identifier
from .query_http import (
    BackupSelection,
    CloudSubmission,
    LocalScopedSubmission,
    OwnedJob,
    PolicySave,
    Role,
    access,
    active,
)


class AdminCloudSubmission(CloudSubmission):
    client_id: Identifier


class AdminSubmission(LocalScopedSubmission):
    client_id: Identifier


def router(config: Config, lifecycle: BackendControl | None = None) -> APIRouter:
    routes = APIRouter(prefix="/admin/query")

    @routes.get("/policy")
    def policy(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return query_policy_store.inspect(config)

    @routes.put("/policy")
    def save(data: PolicySave, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True):
            return query_policy_store.save(config, data.value, data.expected_sha256)

    @routes.get("/tasks")
    def tasks(request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            return query_task_store.listing(db)

    @routes.post("/tasks", status_code=202)
    def submit(data: AdminSubmission, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            active(db, data.client_id)
            route = query_policy_store.checked(config, data.configuration_sha256)
            return local_query_tasks.submit(
                config,
                db,
                route,
                data.client_id,
                ScopedNoteAnswerInput(
                    None if data.vault_ids is None else tuple(data.vault_ids),
                    data.question,
                    data.retrieval_query,
                    data.limit,
                    data.request_id,
                    mode=data.mode,
                ),
            )

    @routes.post("/cloud-tasks", status_code=202)
    def cloud(data: AdminCloudSubmission, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            active(db, data.client_id)
            route = query_policy_store.checked(config, data.configuration_sha256)
            return cloud_answer_tasks.submit(
                config, db, route, data.client_id, data.local_task_id, data.request_id
            )

    @routes.post("/jobs/{job_id}/recover")
    def recover(job_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = query_task_store.job_owner(db, job_id)
            return query_job_control.recover(config, db, cid, job_id)

    @routes.post("/jobs", status_code=202)
    def launch(data: OwnedJob, request: Request) -> dict[str, Any]:
        if lifecycle is not None:
            lifecycle.permit()
        with access(config, request, admin=True) as (db, _):
            active(db, data.client_id)
            if data.role == "local":
                digest = local_query_tasks.get(db, data.client_id, data.task_id)["config_digest"]
            else:
                digest = cloud_answer_tasks.get(db, data.client_id, data.task_id)[1][
                    "routing_digest"
                ]
            route = query_policy_store.version(config, digest)
            job = query_jobs.submit(
                config, db, route, data.client_id, data.role, data.task_id, data.cloud_edit
            )
            query_job_launch.launch(config, data.client_id, job["id"])
            return query_jobs.view(db, data.client_id, job["id"])

    @routes.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = query_task_store.job_owner(db, job_id)
            return query_jobs.cancel(config, db, cid, job_id)

    @routes.get("/tasks/{role}/{task_id}")
    def detail(role: Role, task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = query_task_store.task_owner(db, role, task_id)
            active(db, cid)
            if role == "local":
                return local_query_tasks.status(config, db, cid, task_id)
            value = cloud_answer_tasks.status(db, cid, task_id)
            route = query_policy_store.version(config, value["configuration_sha256"])
            value["backup_choices"] = [
                {
                    "index": i,
                    "model": p.model,
                    "task_limit_nusd": p.task_limit_nusd,
                    "daily_limit_nusd": p.daily_limit_nusd,
                    "input_nusd": p.input_nusd,
                    "output_nusd": p.output_nusd,
                    "currency": p.currency,
                }
                for i, p in enumerate(route.approved_backups, 1)
            ]
            return value

    @routes.post("/tasks/{role}/{task_id}/cancel")
    def cancel_task(role: Role, task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = query_task_store.task_owner(db, role, task_id)
            active(db, cid)
            job_id = query_task_store.task_job(db, cid, role, task_id, include_linked_cloud=True)
            if job_id is not None:
                return query_jobs.cancel(config, db, cid, job_id)
            return (
                local_query_ledger.stop(config, db, cid, task_id)
                if role == "local"
                else cloud_answer_tasks.stop(db, cid, task_id)
            )

    @routes.get("/tasks/{role}/{task_id}/result")
    def result(role: Role, task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            cid = query_task_store.task_owner(db, role, task_id)
            active(db, cid)
            if role == "local":
                return local_query_tasks.deliver(config, db, cid, task_id)
            return cloud_answer_tasks.deliver(config, db, cid, task_id)

    @routes.post("/tasks/cloud/{run_id}/backup")
    def select(run_id: str, data: BackupSelection, request: Request) -> dict[str, Any]:
        with access(config, request, admin=True) as (db, _):
            active(db, data.client_id)
            bound = cloud_answer_tasks.get(db, data.client_id, run_id)[1]
            return cloud_answer_selection.select(
                config,
                db,
                query_policy_store.version(config, bound["routing_digest"]),
                data.client_id,
                run_id,
                data.backup_index,
                data.selection_id,
                allow_switch=True,
            )

    return routes
