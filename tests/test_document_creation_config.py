import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_query_routing_preparation import routing as routing

from fiber_bundle.config import InvalidInput
from fiber_bundle.document_creation_config import SCHEMA, load, parse


@pytest.fixture
def creation_policy(routing):
    return {
        "schema": SCHEMA,
        "enabled": False,
        "executor": "pi",
        "model_config": routing["cloud_answer"],
        "capability_revision": "synthetic-md-v1",
        "formats": ["md"],
    }


def test_closed_config_roundtrip_never_reads_keys_or_probes(creation_policy, tmp_path):
    path = tmp_path / "creation.json"
    path.write_text(json.dumps(creation_policy))
    with (
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("model")),
        patch("sqlite3.connect", side_effect=AssertionError("database")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("subprocess.run", side_effect=AssertionError("process")),
    ):
        policy = load(path)
        assert policy.value() == creation_policy
        assert parse(policy.value()) == policy
        report = policy.inspect()
    assert report["state"] == "prepared" and report["declared_formats"] == ["md"]
    for flag in (
        "execution_allowed",
        "capability_verified",
        "runtime_verified",
        "credentials_read",
    ):
        assert report[flag] is False
    assert report["model_calls"] == 0 and report["automatic_switch"] is False
    wire = json.dumps(report)
    for field in ("model", "endpoint", "secret_file", "image", "capability_revision"):
        assert creation_policy["model_config"].get(field, "not-present") not in wire
    assert not Path(creation_policy["model_config"]["secret_file"]).exists()


def test_distributed_template_is_incomplete_and_stays_disabled():
    policy = load(Path("configs/document-creation.example.json"))
    assert policy.inspect()["state"] == "incomplete"
    with pytest.raises(InvalidInput, match="not_configured"):
        policy.policy()


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", True),
        ("enabled", 0),
        ("schema", "old"),
        ("executor", "shell"),
        ("approved_backups", []),
        ("command", "run"),
        ("formats", []),
        ("formats", ["md", "md"]),
        ("formats", "md"),
        ("formats", ["../pdf"]),
        ("formats", ["PDF"]),
        ("formats", [True]),
        ("formats", ["f" + str(i) for i in range(17)]),
        ("capability_revision", None),
        ("capability_revision", ""),
        ("capability_revision", "../unverified"),
        ("capability_revision", True),
        ("model_config", []),
    ],
)
def test_invalid_capabilities_or_activation_are_rejected(creation_policy, field, value):
    creation_policy[field] = value
    with pytest.raises(InvalidInput):
        parse(creation_policy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_nusd", None),
        ("output_nusd", 0),
        ("task_limit_nusd", None),
        ("daily_limit_nusd", True),
        ("price_revision", ""),
        ("max_attempts", 2),
        ("max_calls", 3),
        ("task_limit_nusd", 1),
        ("daily_limit_nusd", 500000),
        ("image", "executor:latest"),
        ("endpoint", "https://example.com/api"),
        ("model", "../model"),
        ("secret_file", "relative"),
        ("currency", "CNY"),
        ("timezone", "invalid-zone"),
        ("max_request_bytes", 262145),
        ("max_input_tokens", 200001),
        ("max_output_tokens", 8193),
        ("timeout_seconds", 601),
        ("secret_file", "/bad\ud800key"),
    ],
)
def test_invalid_model_or_unknown_unbounded_budgets_are_rejected(creation_policy, field, value):
    creation_policy["model_config"][field] = value
    with pytest.raises(InvalidInput):
        parse(creation_policy)


@pytest.mark.parametrize("field,value", [("formats", ["md"]), ("capability_revision", "v1")])
def test_incomplete_policy_cannot_claim_capabilities(creation_policy, field, value):
    creation_policy.update(model_config=None, formats=[], capability_revision=None)
    creation_policy[field] = value
    with pytest.raises(InvalidInput, match="incomplete"):
        parse(creation_policy)


@pytest.mark.parametrize("change", ["model", "price", "image", "format", "capability", "calls"])
def test_digest_binds_model_limits_and_capabilities(creation_policy, change):
    before = parse(creation_policy).sha256
    changed = copy.deepcopy(creation_policy)
    if change == "format":
        changed["formats"].append("docx")
    elif change == "capability":
        changed["capability_revision"] = "v2"
    else:
        key, value = {
            "model": ("model", "other-model"),
            "price": ("input_nusd", 151),
            "image": ("image", "sha256:" + "e" * 64),
            "calls": ("daily_calls", 3),
        }[change]
        changed["model_config"][key] = value
    assert parse(changed).sha256 != before


def test_constructed_config_is_validated_too(creation_policy):
    policy = parse(creation_policy)
    for malformed in (
        replace(policy, formats=["md"]),
        replace(policy, model_config="bad"),
        replace(policy, capability_revision=None),
    ):
        with pytest.raises(InvalidInput):
            malformed.inspect()


@pytest.mark.parametrize("raw", ["{", '{"enabled":false,"enabled":false}', "NaN"])
def test_strict_json_rejects_duplicates_and_constants(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_text(raw)
    with pytest.raises(InvalidInput):
        load(path)


@pytest.mark.parametrize("kind", ["symlink", "large", "hardlink"])
def test_bounded_regular_configuration_file_only(tmp_path, creation_policy, kind):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(creation_policy))
    if kind == "symlink":
        linked = tmp_path / "link.json"
        linked.symlink_to(path)
        path = linked
    elif kind == "hardlink":
        (tmp_path / "hard.json").hardlink_to(path)
    else:
        path.write_bytes(b" " * 65537)
    with pytest.raises(InvalidInput):
        load(path)
