"""Observable read-only contracts on real SQLite caches and authenticated HTTP."""

import hashlib
import sqlite3
from unittest.mock import patch

import pytest
from test_api import add_client, permissions, vault_ids

from fiber_bundle import database, note_search_build, note_search_cache
from fiber_bundle.gui_capabilities import declared


def cached(http, config):
    cid, _ = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, query=True, add=True, original=True)
    (config.project_root / "test vault/temp_vault1/one.md").write_text(
        "# Synthetic note\nEvidence only.\n"
    )
    note_search_build.build(config, cid, vid)
    return vid, note_search_cache.location(config, vid)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_summary_is_read_only_and_not_freshness_verification(http, config):
    vid, path = cached(http, config)
    job = path.with_suffix(".job.json")
    before = (digest(path), digest(job))
    with (
        patch("fiber_bundle.note_search_cache.fingerprint", side_effect=AssertionError("scan")),
        patch("fiber_bundle.note_search_cache.status", side_effect=AssertionError("status")),
        patch("fiber_bundle.note_inventory.read", side_effect=AssertionError("body")),
        patch("fiber_bundle.note_search_jobs.changed", side_effect=AssertionError("write")),
        patch("fiber_bundle.note_search_jobs.record", side_effect=AssertionError("write")),
    ):
        response = http.get("/admin/note-index/summary")
    assert response.status_code == 200, response.text
    value = response.json()
    row = next(r for r in value["vaults"] if r["vault_id"] == vid)
    assert row["generation_state"] == "ready" and row["indexed_notes"] == 1
    assert row["freshness_verified"] is False and value["freshness_verified"] is False
    assert (digest(path), digest(job)) == before
    assert "/test vault/" not in response.text and "Evidence only" not in response.text
    # External changes do not cause a scan or a misleading freshness claim.
    (config.project_root / "test vault/temp_vault1/one.md").write_text("changed")
    after = http.get("/admin/note-index/summary").json()
    assert next(r for r in after["vaults"] if r["vault_id"] == vid)["generation_state"] == "ready"
    assert (digest(path), digest(job)) == before


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("absent", "absent"),
        ("old", "unsupported"),
        ("empty", "incomplete"),
        ("invalid", "incomplete"),
        ("corrupt", "unreadable"),
        ("symlink", "unreadable"),
    ],
)
def test_each_bad_cache_is_isolated_without_repair(http, config, condition, expected):
    vid, path = cached(http, config)
    if condition == "absent":
        path.unlink()
    elif condition == "corrupt":
        path.write_bytes(b"not a database")
    elif condition == "symlink":
        original = path.with_suffix(".saved")
        path.rename(original)
        path.symlink_to(original.name)
    else:
        with sqlite3.connect(path) as db:
            if condition == "old":
                db.execute("PRAGMA user_version=99")
            elif condition == "empty":
                db.execute("DELETE FROM generation")
            else:
                db.execute("UPDATE generation SET state='impossible'")
    before = digest(path) if path.exists() else None
    response = http.get("/admin/note-index/summary")
    assert response.status_code == 200
    row = next(r for r in response.json()["vaults"] if r["vault_id"] == vid)
    assert row["generation_state"] == expected and row["indexed_notes"] is None
    assert (digest(path) if path.exists() else None) == before


def test_summary_pagination_authorization_and_feature_declarations(http, config):
    assert http.get("/admin/note-index/summary?limit=0").status_code == 422
    first = http.get("/admin/note-index/summary?limit=1").json()
    second = http.get("/admin/note-index/summary?limit=1&offset=1").json()
    assert first["has_more"] and not second["has_more"]
    assert first["vaults"][0]["vault_id"] != second["vaults"][0]["vault_id"]
    features = http.get("/admin/inventory/components").json()["gui_features"]
    assert features["index_summary"] and features["execution_attribution"]
    assert features["unified_workflow"] and not features["runtime_verified"]
    assert not features["local_usage_breakdown"]
    with database.connect(config.state_root) as db:
        assert not any(
            v
            for k, v in declared(db, {}).items()
            if k not in {"contract", "runtime_verified", "local_usage_breakdown"}
        )
        db.execute("PRAGMA user_version=30")
        assert not declared(db, http.app.openapi()["paths"])["unified_workflow"]
        db.execute(f"PRAGMA user_version={database.SCHEMA_VERSION}")
    http.cookies.clear()
    with patch(
        "fiber_bundle.note_search_summary.listing", side_effect=AssertionError("before auth")
    ):
        assert http.get("/admin/note-index/summary").status_code == 401


def test_execution_projection_preserves_old_fields_and_declares_bounds(http, config):
    from fiber_bundle.management_inventory import executions

    # Independent old-schema-compatible table shape, with synthetic historical calls.
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""CREATE TABLE execution_runs(id,client_id,state,attempts,error_code,
      created_at,updated_at,task_limit_nusd,config_digest,binding_kind);
      CREATE TABLE model_calls(id,run_id,model,price_revision,created_at,state,
      reserved_nusd,charged_nusd,billing_day,currency);""")
    db.executemany(
        "INSERT INTO execution_runs VALUES(?, 'c','succeeded',1,NULL,1,2,100,'digest','source')",
        [(f"run-{n:03}",) for n in range(101)],
    )
    db.executemany(
        "INSERT INTO model_calls VALUES(?,'run-000','historical-model','price-v1',"
        "1,'settled',10,7,'2026-10-01','USD')",
        [(f"call-{n}",) for n in range(101)],
    )
    value = executions(db)
    assert value["has_more"] and value["limit"] == 100 and len(value["runs"]) == 100
    run = value["runs"][0]
    assert run["binding_kind"] == "source" and run["updated_at"] == 2
    assert run["calls_has_more"] and len(run["calls"]) == 100
    assert run["calls"][0]["model"] == "historical-model"
    assert run["calls"][0]["price_revision"] == "price-v1"
    assert run["calls"][0]["charged_nusd"] == 7
    db.close()


def test_client_scope_includes_permission_generation(http, config):
    cid, _ = add_client(http)
    vid = vault_ids(http)["personal"]
    permissions(http, cid, vid, query=True, add=True, original=True)

    def revision():
        row = next(c for c in http.get("/admin/clients").json() if c["id"] == cid)
        return next(g for g in row["grants"] if g["vault_id"] == vid)["permission_revision"]

    before = revision()
    permissions(http, cid, vid, query=False, add=False, original=False)
    permissions(http, cid, vid, query=True, add=True, original=True)
    assert revision() > before
