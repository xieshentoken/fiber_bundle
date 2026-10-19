import json

import pytest
from test_api import add_client, permissions, vault_ids

from fiber_bundle import note_search_build
from fiber_bundle.agent_connections.evidence import merge, project
from fiber_bundle.agent_connections.turn import TurnBlocked

from .support import Harness


@pytest.fixture
def integration(http, config):
    h = Harness()
    cid, headers = add_client(http, "synthetic-agent")
    ids = vault_ids(http)
    vid = ids["personal"]
    permissions(http, cid, vid, query=True)
    h.configure(vault_ids=[vid])
    h.context["client_id"] = cid
    path = config.project_root / "test vault/temp_vault1/synthetic.md"
    path.write_text("# Synthetic experiment\nSynthetic coolant target: 37 C\n")
    other = config.project_root / "test vault/temp_vault2/private.md"
    other.write_text("# Not authorized\nSynthetic coolant target: private 999 C\n")

    def retrieve(query, vaults, limit):
        h.events.append("retrieve")
        response = http.post(
            "/v1/search",
            headers=headers,
            json={"query": query, "vault_ids": list(vaults), "limit": limit},
        )
        response.raise_for_status()
        return response.json()

    def verify(prepared):
        # Fixture uses the real query endpoint to reauthorize and reread current source bytes.
        # This is not a production transport: A2/A4 supply their bounded verification path.
        current = retrieve("coolant", (vid,), 5)
        assert merge([project(current, (vid,), 5)], 8000) == json.loads(prepared.evidence_json)

    return h, {"retrieve": retrieve, "verify_evidence": verify}, cid, vid, path


def test_real_search_is_query_only_and_does_not_expand_to_other_vault(http, integration):
    h, callbacks, _, vid, _ = integration
    result = h.run(**callbacks)
    assert len(result["sources"]) == 1 and result["sources"][0]["vault_id"] == vid
    assert "37 C" in result["sources"][0]["snippet"]
    assert "private 999 C" not in json.dumps(result)
    assert "originals" not in result["sources"][0]
    assert h.events.count("generate") == 1


@pytest.mark.parametrize("change", ["revoke", "scope", "stale"])
def test_real_api_failure_prevents_generation(http, config, integration, change):
    h, callbacks, cid, vid, path = integration
    if change == "revoke":
        assert http.post("/admin/clients/" + cid + "/revoke").status_code == 200
    elif change == "scope":
        permissions(http, cid, vid, query=False)
    else:
        assert note_search_build.build(config, cid, vid)["state"] == "ready"
        path.write_text("# Changed\nSynthetic coolant target: 42 C\n")
    with pytest.raises(TurnBlocked, match="^agent_retrieval_failed$"):
        h.run(**callbacks)
    assert h.events == ["retrieve"]


@pytest.mark.parametrize("change", ["permission", "content"])
def test_actual_permission_or_bytes_changed_during_generation_blocks_delivery(
    http, integration, change
):
    h, callbacks, cid, vid, path = integration

    def generate(prepared):
        raw = h.generate(prepared)
        if change == "permission":
            permissions(http, cid, vid, query=False)
        else:
            path.write_text("# Changed\nSynthetic coolant target: 42 C\n")
        return raw

    with pytest.raises(TurnBlocked, match="^agent_evidence_changed$"):
        h.run(**callbacks, generate=generate)
    assert h.events.count("generate") == 1
