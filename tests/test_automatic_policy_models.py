"""Automatic ingestion resolves the model actually selected in the shared catalog."""

from dataclasses import asdict, replace

import pytest
from test_publication_layout import activate

from fiber_bundle import (
    automatic_jobs,
    automatic_policy,
    database,
    query_policy_store,
    vault_save_contract,
)
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_configuration import policy as models
from fiber_bundle.model_configuration import store as catalog
from fiber_bundle.model_configuration.contracts import Change, Save

pytest_plugins = ["test_model_configuration", "test_source_imports", "test_execution"]


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    monkeypatch.setattr("fiber_bundle.automatic_ingestion.Runtime.start", lambda self: None)


@pytest.fixture
def scope(config, owner):
    client, _, ids = owner
    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, ids["personal"], **settings)
    return client, ids["personal"]


def register(config, provider="opencodego", model="glm-5.3-flash"):
    with database.connect(config.state_root) as db:
        return catalog.save(
            config,
            db,
            Save(provider=provider, name="Synthetic", model=model, api_key="synthetic-key"),
        )["id"]


def settings(scope, reference):
    return automatic_policy.Policy(
        policy_version=2,
        enabled=True,
        client_id=scope[0],
        processing_mode="pi_skill",
        model_configuration_id=reference,
        allow_model_source_content=True,
        allowed_kinds=["document", "article", "intake", "media", "expert"],
    )


@pytest.mark.parametrize("provider,model", [("opencodego", "glm-5.3-flash"), ("openai", "gpt-5")])
def test_listed_model_saves_and_binds_without_legacy_config(
    config, http, scope, vault_keychain, provider, model
):
    identity = register(config, provider, model)
    url = f"/admin/vault-catalog/{scope[1]}/automatic-policy"
    listed = http.get(url).json()
    assert identity in [m["id"] for m in listed["models"]]
    assert query_policy_store.legacy_current(config) is None
    value = settings(scope, identity)
    result = http.put(url, json={"expected_revision": 0, "settings": value.model_dump()})
    assert result.status_code == 200, result.json()
    assert http.get(url).json()["settings"] == value.model_dump()
    with database.connect(config.state_root) as db:
        for kind in value.allowed_kinds:
            binding = automatic_policy.binding(config, db, scope[1], scope[0], kind)
            if kind == "intake":
                assert "processing_model_sha256" not in binding
            else:
                assert binding["processing_model_sha256"] == models.resolve(config, identity).digest
        assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM automatic_jobs").fetchone()[0] == 0


def test_explicit_selection_is_independent_of_default_and_jobs_detect_changes(
    config, store, scope, vault_keychain
):
    identity = register(config)
    other = register(config, "openai", "gpt-5")
    with database.connect(config.state_root) as db:
        catalog.change(db, other, Change(action="default", expected_revision=1))
        automatic_policy.save(config, db, scope[1], 0, settings(scope, identity))
        job_id = automatic_jobs.enqueue(config, db, *scope, "article", "synthetic-source")
        job = automatic_jobs.get(db, job_id)
        assert job["value"]["binding"]["processing_model_sha256"] == models.resolve(
            config, identity
        ).digest
        catalog.change(db, identity, Change(action="disable", expected_revision=1))
        with pytest.raises(InvalidInput, match="model_disabled"):
            automatic_jobs.authorize(config, db, job)
        catalog.change(db, identity, Change(action="enable", expected_revision=2))
        with pytest.raises(InvalidInput, match="automatic_authorization_changed"):
            automatic_jobs.authorize(config, db, job)


@pytest.mark.parametrize("fault", ["missing", "disabled", "credential_disabled", "consent"])
def test_invalid_model_or_missing_consent_does_not_change_policy(
    config, http, scope, vault_keychain, fault
):
    identity = register(config)
    with database.connect(config.state_root) as db:
        if fault == "disabled":
            catalog.change(db, identity, Change(action="disable", expected_revision=1))
        if fault == "credential_disabled":
            db.execute("UPDATE model_credentials SET active=0")
    value = settings(scope, "model_" + "0" * 32 if fault == "missing" else identity)
    if fault == "consent":
        value.allow_model_source_content = False
    url = f"/admin/vault-catalog/{scope[1]}/automatic-policy"
    result = http.put(url, json={"expected_revision": 0, "settings": value.model_dump()})
    assert result.status_code == 422, result.json()
    code = {
        "missing": "model_unconfigured",
        "disabled": "model_disabled",
        "credential_disabled": "model_disabled",
        "consent": "processing_cloud_content_not_allowed",
    }[fault]
    assert code in result.text
    assert http.get(url).json()["revision"] == 0


def test_default_reference_and_explicit_legacy_reference_are_resolved_separately(
    config, store, vault_keychain, policy
):
    policy = replace(policy, max_attempts=1)
    query_policy_store.save(
        config,
        {
            "schema": "query-routing-preparation/v1",
            "enabled": False,
            "local_query": None,
            "cloud_answer": asdict(policy),
            "approved_backups": [],
        },
        None,
    )
    identity = register(config, "openai", "gpt-5")
    with database.connect(config.state_root) as db:
        catalog.change(db, identity, Change(action="default", expected_revision=1))
    value = {"allow_model_source_content": True, "model_configuration_id": "app-default"}
    assert automatic_policy.processing_route(config, value).configuration_id == identity
    value["model_configuration_id"] = "opencodego-primary"
    assert automatic_policy.processing_route(config, value).digest == policy.digest


def test_legacy_reference_does_not_silently_use_registered_default(
    config, store, vault_keychain
):
    identity = register(config)
    with database.connect(config.state_root) as db:
        catalog.change(db, identity, Change(action="default", expected_revision=1))
    with pytest.raises(InvalidInput, match="processing_model_unconfigured"):
        automatic_policy.processing_route(
            config,
            {"allow_model_source_content": True, "model_configuration_id": "opencodego-primary"},
        )


def test_active_legacy_inbox_reports_layout_requirement_instead_of_missing_model(
    config, http, owner, vault_keychain
):
    client, _, ids = owner
    activate(config, ids["personal"])
    identity = register(config)
    url = f"/admin/vault-catalog/{ids['personal']}/automatic-policy"
    result = http.put(
        url,
        json={
            "expected_revision": 0,
            "settings": settings((client, ids["personal"]), identity).model_dump(),
        },
    )
    assert result.status_code == 422
    assert result.json()["error"] == "automatic_policy_requires_unified_layout"
    assert "统一知识库目录" in result.json()["message"]
    assert "模型" not in result.json()["message"]
    assert http.get(url).json()["revision"] == 0
