"""Explicit, revisioned host activation. Artifact descriptors alone never enable a task."""

from dataclasses import asdict, dataclass

from ..config import Config, InvalidInput, contained
from ..inputs import canonical
from ..query_policy_store import write_private
from ..query_routing_files import read
from .contracts import Engine, Model, fingerprint, object_fields, read_json
from .worker import Policy as WorkerPolicy


@dataclass(frozen=True)
class Profile:
    revision: int
    enabled: bool
    image: str
    engine: Engine
    timeout_seconds: int = 180

    @property
    def digest(self) -> str:
        return fingerprint(asdict(self))

    def validate(self) -> None:
        if type(self.revision) is not int or self.revision < 1 or type(self.enabled) is not bool:
            raise InvalidInput("graph_invalid_policy")
        self.engine.validate()
        WorkerPolicy(self.image, self.timeout_seconds).validate()


def parse(value: object) -> Profile:
    data = object_fields(value, set(Profile.__dataclass_fields__))
    engine = object_fields(data["engine"], set(Engine.__dataclass_fields__))
    model = engine["model"]
    if model is not None:
        model = Model(**object_fields(model, set(Model.__dataclass_fields__)))
    profile = Profile(**{**data, "engine": Engine(**{**engine, "model": model})})
    profile.validate()
    return profile


def current(config: Config, *, require_enabled: bool = False) -> Profile | None:
    path = contained(config.state_root, "knowledge-graph-policy.json")
    profile = parse(read_json(read(path, 16384), 16384)) if path.exists() else None
    if require_enabled and (profile is None or not profile.enabled):
        raise InvalidInput("knowledge_graph_disabled")
    return profile


def save(config: Config, profile: Profile, expected: str | None) -> None:
    # Caller authenticates admin and holds storage lock; read operations never create state.
    profile.validate()
    prior = current(config)
    if (prior.digest if prior else None) != expected or profile.revision != (
        prior.revision + 1 if prior else 1
    ):
        raise InvalidInput("graph_policy_revision_conflict")
    write_private(
        contained(config.state_root, "knowledge-graph-policy.json"),
        canonical(asdict(profile)).encode(),
    )
