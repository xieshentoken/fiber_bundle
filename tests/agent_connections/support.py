"""Synthetic fixtures; no runtime credentials or real Vault content."""

import copy
import hashlib
import json

from fiber_bundle.agent_connections.contracts import ConnectionSpec, TurnRequest
from fiber_bundle.agent_connections.turn import run


def context(**changes):
    spec = ConnectionSpec(name="Synthetic Agent", agent_type="hermes", vault_ids=["personal"])
    result = {
        "connection_id": "connection-one",
        "client_id": "client-one",
        "revision": 1,
        "credential_generation": 1,
        "authorization_sha256": "a" * 64,
        "configuration_sha256": spec.digest,
        "configuration": spec.model_dump(),
        "enabled": True,
    }
    result.update(changes)
    return result


def passage(**changes):
    result = {
        "citation": "[1]",
        "vault_id": "personal",
        "note_path": "synthetic.md",
        "title": "Synthetic source",
        "sha256": hashlib.sha256(b"Synthetic coolant target: 37 C").hexdigest(),
        "line": 1,
        "line_end": 1,
        "snippet": "Synthetic coolant target: 37 C",
        "source_kind": "historical_note",
        "source_label": "历史笔记，来源未核实",
    }
    result.update(changes)
    return result


def envelope(rows=None, vaults=None):
    rows = [passage()] if rows is None else rows
    return {
        "scope": "vault_notes",
        "searched_vault_ids": vaults or ["personal"],
        "status": "evidence_found" if rows else "no_evidence",
        "results": rows,
    }


def proposal(claims=None, supplement=None):
    return json.dumps(
        {
            "schema_version": "fiber-agent-answer/v1",
            "claims": claims
            if claims is not None
            else [{"text": "Synthetic coolant target is 37 C.", "citations": ["[1]"]}],
            "model_supplement": supplement,
        }
    )


class Harness:
    def __init__(self):
        self.context = context()
        self.request = TurnRequest(
            request_id="synthetic-turn",
            question="What is the coolant target?",
            retrieval_queries=["coolant"],
        )
        self.events = []
        self.response = envelope()
        self.raw = proposal()
        self.stopped = False

    def configure(self, **changes):
        spec = ConnectionSpec.model_validate({**self.context["configuration"], **changes})
        self.context.update(configuration=spec.model_dump(), configuration_sha256=spec.digest)

    def retrieve(self, query, vaults, limit):
        self.events.append("retrieve")
        assert query in self.request.retrieval_queries
        assert vaults == tuple(self.request.vault_ids or self.context["configuration"]["vault_ids"])
        assert limit <= 5
        return copy.deepcopy(self.response)

    def verify(self, prepared):
        self.events.append("verify")

    def generate(self, prepared):
        self.events.append("generate")
        assert json.loads(prepared.user_message)["question"] == self.request.question
        return self.raw

    def run(self, **callbacks):
        options = {
            "read_context": lambda: self.context,
            "retrieve": self.retrieve,
            "verify_evidence": self.verify,
            "generate": self.generate,
            "should_stop": lambda: self.stopped,
        }
        return run(self.request, **(options | callbacks))
