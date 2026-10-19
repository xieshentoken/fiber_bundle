"""Answer task creation and status transport; execution stays with the independent worker."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import answer_submissions, answer_tasks, auth, database
from .answer_payloads import NoteSubmission, ScopedSubmission, Submission
from .config import Config
from .execution_config import ExecutionConfig


def router(config: Config, policy: ExecutionConfig | None) -> APIRouter:
    routes = APIRouter()

    def enqueue(
        data: answer_tasks.AnswerInput
        | answer_tasks.NoteAnswerInput
        | answer_tasks.ScopedNoteAnswerInput,
        request: Request,
    ) -> Any:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            if policy is None:
                return JSONResponse({"error": "answer_execution_not_configured"}, status_code=503)
            return answer_submissions.submit(config, db, policy, client["id"], data)
        except answer_submissions.SubmissionConflict:
            return JSONResponse({"error": "answer_request_conflict"}, status_code=409)
        finally:
            db.close()

    @routes.post("/v1/answer-tasks", status_code=202)
    def submit(data: Submission, request: Request) -> Any:
        return enqueue(
            answer_tasks.AnswerInput(
                data.receipt_id,
                data.session_id,
                data.question,
                data.retrieval_query,
                data.limit,
                data.request_id,
                mode=data.mode,
            ),
            request,
        )

    @routes.post("/v1/note-answer-tasks", status_code=202)
    def submit_notes(data: NoteSubmission, request: Request) -> Any:
        return enqueue(
            answer_tasks.NoteAnswerInput(
                data.vault_id,
                data.question,
                data.retrieval_query,
                data.limit,
                data.request_id,
                mode=data.mode,
            ),
            request,
        )

    @routes.post("/v1/scoped-note-answer-tasks", status_code=202)
    def submit_scoped_notes(data: ScopedSubmission, request: Request) -> Any:
        return enqueue(
            answer_tasks.ScopedNoteAnswerInput(
                None if data.vault_ids is None else tuple(data.vault_ids),
                data.question,
                data.retrieval_query,
                data.limit,
                data.request_id,
                mode=data.mode,
            ),
            request,
        )

    @routes.get("/v1/answer-tasks/{run_id}")
    def status(run_id: str, request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            return answer_submissions.status(config, db, client["id"], run_id)
        finally:
            db.close()

    return routes
