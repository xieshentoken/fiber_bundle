"""Real cloud HTTP data must satisfy the shipped frontend's v2 contract."""

import json
import shutil
import subprocess
from pathlib import Path

from .test_cloud import library as library


def validate_frontend(value, found, parameters):
    node = shutil.which("node")
    assert node, "Node validates the actual shipped browser-side data boundary"
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs';"
            "import {noteCloudSnapshot,noteCloudSearch} "
            "from './src/fiber_bundle/web/note_cloud_model.js';"
            "const {value,found,scope}=JSON.parse(readFileSync(0,'utf8'));"
            "noteCloudSnapshot(value,scope);noteCloudSearch(found,scope);",
        ],
        input=json.dumps({"value": value, "found": found, "scope": parameters}),
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_http_snapshot_and_search_satisfy_actual_frontend_contract(http, library):
    client, vault, _ = library
    parameters = {"client_id": client, "vault_id": vault}
    snapshot = http.get("/admin/knowledge-graph/note-cloud", params=parameters)
    search = http.get(
        "/admin/knowledge-graph/note-cloud-search", params={**parameters, "query": "Alpha"}
    )
    assert snapshot.status_code == search.status_code == 200
    value, found = snapshot.json(), search.json()
    assert value["schema"] == found["schema"] == "fiber-note-cloud/v2"
    assert value["snapshot_token"] == found["snapshot_token"]
    assert found["items"] and found["items"][0]["id"] in {n["id"] for n in value["nodes"]}
    assert len(value["nodes"]) == 4
    validate_frontend(value, found, parameters)


def test_real_experts_orphans_and_ownership_satisfy_shipped_frontend(http, config):
    from note_cloud_acceptance_server import small_library
    from note_cloud_complete_fixture import small_cases, validate_complete

    from fiber_bundle import auth, database, note_search_build

    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "Cross-layer synthetic reader", False)
        vault = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
        auth.set_grant(db, client["id"], vault, query=True, original=True, add=True)
    root = config.project_root / "test vault/temp_vault1"
    small_library(root)
    cases = small_cases(config, client, vault, root)
    note_search_build.build(config, client["id"], vault)
    parameters = {"client_id": client["id"], "vault_id": vault}
    value = http.get("/admin/knowledge-graph/note-cloud", params=parameters).json()
    found = http.get(
        "/admin/knowledge-graph/note-cloud-search", params={**parameters, "query": "专家"}
    ).json()
    assert value["snapshot_token"] == found["snapshot_token"]
    assert value["coverage"]["current_experts"] == 2
    assert value["coverage"]["unresolved_targets"] == 2
    validate_complete(value, cases, "personal")
    validate_frontend(value, found, parameters)
