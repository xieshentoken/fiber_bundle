"""Authenticated answer transport; evidence selection and validation live outside the route."""

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field

from . import answer_tasks, auth, database, evidence_answers
from .config import Config
from .payloads import Identifier, Payload


class AnswerRequest(Payload):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    retrieval_query: Annotated[str, Field(min_length=1, max_length=200)]
    vault_ids: Annotated[list[Identifier], Field(max_length=20)] | None = None
    session_id: Identifier | None = None
    limit: Annotated[int, Field(ge=1, le=20)] = 10


def router(config: Config) -> APIRouter:
    routes = APIRouter()

    @routes.post("/v1/answers")
    def answer(data: AnswerRequest, request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            return evidence_answers.answer(
                config,
                db,
                client["id"],
                data.question,
                data.retrieval_query,
                data.vault_ids,
                data.limit,
                session_id=data.session_id,
            )
        finally:
            db.close()

    @routes.get("/v1/answers/{run_id}")
    def result(run_id: str, request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            client = auth.client(db, request.headers.get("authorization", ""))
            return answer_tasks.deliver(config, db, client["id"], run_id)
        finally:
            db.close()

    return routes
