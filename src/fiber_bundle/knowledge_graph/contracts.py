"""Versioned bounded Worker input. No tool imports, paths, credentials or storage writes."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from ..answer_model_output import reject_constant, unique_object
from ..config import InvalidInput
from ..inputs import canonical
from ..moc_store import stable_identity
from ..note_changes import identifier

SCHEMA = "fiber-knowledge-graph/v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_NOTES = 100
MAX_NOTE_BYTES = 64 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_ENTITIES = 2000
MAX_RELATIONS = 2000
SEMANTICA_REVISION = "320761de5d040a54a3220acc563223b4a7ffdc51"
DIGEST = re.compile(r"[0-9a-f]{64}")
KINDS = {
    "historical_note",
    "published_article",
    "published_document",
    "published_media",
    "user_revision",
    "agent_analysis",
}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def digest(value: str) -> None:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise InvalidInput("graph_invalid_digest")


def bounded_text(value: str, maximum: int) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or "\0" in value:
        raise InvalidInput("graph_invalid_text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidInput("graph_invalid_unicode") from None


def object_fields(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise InvalidInput("graph_invalid_fields")
    return value


def read_json(raw: bytes, maximum: int) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= maximum:
        raise InvalidInput("graph_protocol_byte_limit")
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidInput("graph_invalid_json") from None
    if not isinstance(value, dict):
        raise InvalidInput("graph_invalid_object")
    return value


@dataclass(frozen=True)
class Model:
    name: str
    revision: str
    files_sha256: str
    dimensions: int

    def validate(self) -> None:
        bounded_text(self.name, 200)
        bounded_text(self.revision, 100)
        digest(self.files_sha256)
        if type(self.dimensions) is not int or not 1 <= self.dimensions <= 8192:
            raise InvalidInput("graph_invalid_model_dimensions")


@dataclass(frozen=True)
class Engine:
    name: str = "semantica"
    version: str = "0.7.0"
    revision: str = SEMANTICA_REVISION
    algorithm: str = "graph_builder"
    model: Model | None = None
    merge_entities: bool = False
    resolve_conflicts: bool = False
    unknown_relation_endpoint: str = "reject"

    def validate(self) -> None:
        for value in (self.name, self.version, self.revision, self.algorithm):
            bounded_text(value, 100)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name) is None:
            raise InvalidInput("graph_invalid_engine_name")
        if self.name == "semantica" and (self.version, self.revision) != (
            "0.7.0",
            SEMANTICA_REVISION,
        ):
            raise InvalidInput("graph_semantica_pin_mismatch")
        if (
            self.merge_entities is not False
            or self.resolve_conflicts is not False
            or self.unknown_relation_endpoint != "reject"
        ):
            raise InvalidInput("graph_unsafe_engine_options")
        if self.model is not None:
            self.model.validate()


@dataclass(frozen=True)
class Source:
    note_id: str
    sha256: str
    text_sha256: str
    title: str
    text: str
    source_kind: str
    source_version_id: str | None

    def validate(self) -> None:
        stable_identity(self.note_id)
        digest(self.sha256)
        digest(self.text_sha256)
        bounded_text(self.title, 200)
        bounded_text(self.text, MAX_NOTE_BYTES)
        if (
            len(self.text.encode()) > MAX_NOTE_BYTES
            or hashlib.sha256(self.text.encode()).hexdigest() != self.text_sha256
        ):
            raise InvalidInput("graph_invalid_source_text")
        if not isinstance(self.source_kind, str) or self.source_kind not in KINDS:
            raise InvalidInput("graph_invalid_source_kind")
        if self.source_version_id is not None:
            identifier(self.source_version_id)
        if self.source_kind.startswith("published_") and self.source_version_id is None:
            raise InvalidInput("graph_missing_source_version")
        if self.source_kind in {"historical_note", "agent_analysis"} and self.source_version_id:
            raise InvalidInput("graph_invalid_source_version")


@dataclass(frozen=True)
class Request:
    task_id: str
    client_id: str
    vault_id: str
    engine: Engine
    notes: tuple[Source, ...]
    schema: str = SCHEMA

    def validate(self) -> None:
        if self.schema != SCHEMA:
            raise InvalidInput("graph_unsupported_schema")
        for value in (self.task_id, self.client_id, self.vault_id):
            identifier(value)
        self.engine.validate()
        if not 1 <= len(self.notes) <= MAX_NOTES:
            raise InvalidInput("graph_note_limit")
        for note in self.notes:
            note.validate()
        ids = [note.note_id for note in self.notes]
        if ids != sorted(set(ids)):
            raise InvalidInput("graph_duplicate_or_unsorted_sources")
        if sum(len(note.text.encode()) for note in self.notes) > MAX_TEXT_BYTES:
            raise InvalidInput("graph_total_text_limit")

    @property
    def configuration_sha256(self) -> str:
        return fingerprint(asdict(self.engine))

    @property
    def input_sha256(self) -> str:
        return fingerprint(asdict(self))

    @property
    def idempotency_key(self) -> str:
        value = asdict(self)
        del value["task_id"]
        return fingerprint(value)

    def encode(self) -> bytes:
        self.validate()
        raw = canonical(asdict(self)).encode()
        if len(raw) > MAX_INPUT_BYTES:
            raise InvalidInput("graph_protocol_byte_limit")
        return raw


def decode_request(raw: bytes) -> Request:
    value = object_fields(
        read_json(raw, MAX_INPUT_BYTES),
        {"schema", "task_id", "client_id", "vault_id", "engine", "notes"},
    )
    engine = object_fields(value["engine"], set(Engine.__dataclass_fields__))
    model = engine["model"]
    if model is not None:
        model = Model(**object_fields(model, set(Model.__dataclass_fields__)))
    engine = {**engine, "model": model}
    if not isinstance(value["notes"], list) or not 1 <= len(value["notes"]) <= MAX_NOTES:
        raise InvalidInput("graph_note_limit")
    notes = tuple(
        Source(**object_fields(note, set(Source.__dataclass_fields__))) for note in value["notes"]
    )
    request = Request(
        value["task_id"],
        value["client_id"],
        value["vault_id"],
        Engine(**engine),
        notes,
        value["schema"],
    )
    request.validate()
    return request
