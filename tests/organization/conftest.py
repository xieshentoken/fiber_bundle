import json

import pytest

from fiber_bundle import auth, database
from fiber_bundle.objects import digest


@pytest.fixture
def scope(config, store):
    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "organization test", False)
        vid = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
        auth.set_grant(db, client["id"], vid, query=True, original=True, add=True)
    root = config.project_root / "test vault/temp_vault1"
    return client["id"], vid, root


@pytest.fixture
def package(scope):
    root = scope[2] / "Book/fake-thermal"
    files = {
        "SKILL.md": b"# Thermal Draft\n",
        "references/runtime/catalog.json": json.dumps(
            {
                "schema_version": "tkc.runtime-catalog/v0.3",
                "runtime_version": "reference-search-bm25-v0.3",
                "objects": [],
                "figures": [],
                "package_id": "fake-thermal",
                "module_id": "thermal",
                "policy": {
                    "assurance": {
                        "artifact_stage": "draft",
                        "review_state": "deferred",
                        "knowledge_verified": False,
                    }
                },
                "capabilities": {"reference": True},
                "conflicts": [],
                "gaps": [],
            }
        ).encode(),
        "assets/test.png": b"private image",
        "scripts/query_reference.py": b'raise RuntimeError("NEVER EXECUTE")',
    }
    for name, raw in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    manifest = {
        "schema_version": "tkc.expert-skill/v0.1",
        "build": {"compiler": "fake-expert"},
        "package": {
            "id": "fake-thermal",
            "name": "Thermal Draft",
            "version": "1",
            "status": "draft",
        },
        "capabilities": {"reference": True},
        "sources": [{"sha256": "a" * 64}],
        "integrity": {"files": {n: digest(b) for n, b in files.items()}},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root
