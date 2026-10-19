"""Owned query intents and results. Client requests never launch or approve models."""

from typing import Any

from fastapi import APIRouter, Request

from . import (
    answer_tasks,
    cloud_answer_tasks,
    local_query_ledger,
    local_query_tasks,
    query_jobs,
    query_policy_store,
    query_task_store,
)
from .config import Config
from .query_http import (
    CloudSubmission,
    LocalNoteSubmission,
    LocalScopedSubmission,
    LocalSubmission,
    Role,
    access,
)


def router(config: Config) -> APIRouter:
    routes = APIRouter(prefix="/v1")

    def enqueue(
        request: Request, digest: str, data: local_query_tasks.AnswerInput
    ) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            route = query_policy_store.checked(config, digest)
            return local_query_tasks.submit(config, db, route, cid, data)

    @routes.post("/local-query-tasks", status_code=202)
    def receipt(data: LocalSubmission, request: Request) -> dict[str, Any]:
        return enqueue(
            request,
            data.configuration_sha256,
            answer_tasks.AnswerInput(
                data.receipt_id,
                data.session_id,
                data.question,
                data.retrieval_query,
                data.limit,
                data.request_id,
                mode=data.mode,
            ),
        )

    @routes.post("/local-note-query-tasks", status_code=202)
    def notes(data: LocalNoteSubmission, request: Request) -> dict[str, Any]:
        return enqueue(
            request,
            data.configuration_sha256,
            answer_tasks.NoteAnswerInput(
                data.vault_id,
                data.question,
                data.retrieval_query,
                data.limit,
                data.request_id,
                mode=data.mode,
            ),
        )

    @routes.post("/local-scoped-query-tasks", status_code=202)
    def scoped(data: LocalScopedSubmission, request: Request) -> dict[str, Any]:
        return enqueue(
            request,
            data.configuration_sha256,
            answer_tasks.ScopedNoteAnswerInput(
                None if data.vault_ids is None else tuple(data.vault_ids),
                data.question,
                data.retrieval_query,
                data.limit,
                data.request_id,
                mode=data.mode,
            ),
        )

    @routes.post("/cloud-answer-tasks", status_code=202)
    def cloud(data: CloudSubmission, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            return cloud_answer_tasks.submit(
                config,
                db,
                query_policy_store.checked(config, data.configuration_sha256),
                cid,
                data.local_task_id,
                data.request_id,
            )

    @routes.get("/query-tasks/{role}/{task_id}")
    def status(role: Role, task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            return (
                local_query_tasks.status(config, db, cid, task_id)
                if role == "local"
                else cloud_answer_tasks.status(db, cid, task_id)
            )

    @routes.get("/query-tasks/{role}/{task_id}/result")
    def result(role: Role, task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            if role == "local":
                return local_query_tasks.deliver(config, db, cid, task_id)
            return cloud_answer_tasks.deliver(config, db, cid, task_id)

    @routes.post("/query-tasks/{role}/{task_id}/cancel")
    def cancel(role: Role, task_id: str, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            job_id = query_task_store.task_job(db, cid, role, task_id)
            if job_id is not None:
                return query_jobs.cancel(config, db, cid, job_id)
            return (
                local_query_ledger.stop(config, db, cid, task_id)
                if role == "local"
                else cloud_answer_tasks.stop(db, cid, task_id)
            )

    @routes.get("/query-jobs/{job_id}")
    def job(job_id: str, request: Request) -> dict[str, Any]:
        with access(config, request) as (db, cid):
            assert cid is not None
            return query_jobs.view(db, cid, job_id)

    return routes
