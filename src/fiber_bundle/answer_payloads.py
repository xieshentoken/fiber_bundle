"""Shared answer request contracts, independent of HTTP route assembly."""

from typing import Annotated

from pydantic import Field

from .answer_generation import AnswerMode
from .payloads import Identifier, Payload


class Submission(Payload):
    request_id: Identifier
    receipt_id: Identifier
    session_id: Identifier
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    retrieval_query: Annotated[str, Field(min_length=1, max_length=200)]
    limit: Annotated[int, Field(ge=1, le=20)] = 10
    mode: AnswerMode = "extractive"


class NoteSubmission(Payload):
    request_id: Identifier
    vault_id: Identifier
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    retrieval_query: Annotated[str, Field(min_length=1, max_length=200)]
    limit: Annotated[int, Field(ge=1, le=20)] = 10
    mode: AnswerMode = "extractive"


class ScopedSubmission(Payload):
    request_id: Identifier
    vault_ids: Annotated[list[Identifier], Field(max_length=20)] | None = None
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    retrieval_query: Annotated[str, Field(min_length=1, max_length=200)]
    limit: Annotated[int, Field(ge=1, le=20)] = 10
    mode: AnswerMode = "extractive"
