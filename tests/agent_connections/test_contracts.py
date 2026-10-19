import pytest

from fiber_bundle.agent_connections.contracts import ConnectionContext, ConnectionSpec, TurnRequest
from fiber_bundle.config import InvalidInput

from .support import context


@pytest.mark.parametrize(
    "change",
    [
        {"vault_ids": []},
        {"vault_ids": ["a", "a"]},
        {"vault_ids": ["\na"]},
        {"vault_ids": ["a"] * 21},
        {"name": " "},
        {"name": "bad\nname"},
        {"api_key": "synthetic-secret"},
        {"base_url": "http://example.invalid"},
        {"agent_type": "arbitrary-program"},
        {"model_configuration_id": "../secret.json"},
        {"agent_type": "mcp", "model_configuration_id": "local"},
        {"limits": {"queries": 3}},
        {"limits": {"queries": True}},
        {"limits": {"results_per_query": 6}},
        {"limits": {"evidence_chars": 16001}},
        {"limits": {"timeout_seconds": 181}},
        {"limits": {"provider_calls": 2}},
        {"limits": {"output_tokens": 4097}},
    ],
)
def test_configuration_rejects_unsupported_or_secret_fields(change):
    with pytest.raises((ValueError, InvalidInput)):
        ConnectionSpec.model_validate(context()["configuration"] | change)


def test_configuration_digest_and_identity_are_bound():
    valid = context()
    assert (
        ConnectionContext.model_validate(valid).configuration.digest
        == valid["configuration_sha256"]
    )
    valid["configuration"]["vault_ids"] = ["another-vault"]
    with pytest.raises(ValueError, match="configuration_digest_mismatch"):
        ConnectionContext.model_validate(valid)


@pytest.mark.parametrize(
    "queries", [[], ["x", "x"], ["x", "y", "z"], ["*"], ["\nx"], ["a b c d e f g h i"]]
)
def test_retrieval_terms_are_explicit_and_bounded(queries):
    with pytest.raises((ValueError, InvalidInput)):
        TurnRequest(request_id="one", question="A long question", retrieval_queries=queries)
