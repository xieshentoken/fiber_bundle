import json

import pytest
from fastapi.testclient import TestClient

from fiber_bundle import auth
from fiber_bundle.api import create_app
from fiber_bundle.config import load_config
from fiber_bundle.database import initialize
from fiber_bundle.inputs import AssetInput
from fiber_bundle.store import Store


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "state_root": ".state",
                "vaults": {
                    "personal": "test vault/temp_vault1",
                    "supplier": "test vault/temp_vault2",
                },
            }
        )
    )
    return load_config(path, tmp_path)


@pytest.fixture
def store(config):
    initialize(config)
    return Store(config)


@pytest.fixture
def request_data():
    return dict(
        ingress_scope="synthetic:owner",
        event_id="event-1",
        vault="personal",
        source_key="synthetic-source",
        raw_event=b'{"synthetic":true,"event":"1"}',
        items=(
            AssetInput("原文一".encode(), "body.txt", "text/plain", "body"),
            AssetInput(bytes(range(256)), "sample.bin", "application/octet-stream"),
        ),
        session_scope="synthetic:owner",
        session_id="session-1",
        retention="knowledge",
    )


PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def http(config, store):
    auth.set_password(config, PASSWORD)
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49000)
    ) as client:
        response = client.post(
            "/admin/login", json={"password": PASSWORD}, headers={"Origin": "http://127.0.0.1:8765"}
        )
        assert response.status_code == 200
        client.headers.update(
            {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": response.json()["csrf"]}
        )
        yield client
