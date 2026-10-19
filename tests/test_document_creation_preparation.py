import json
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

import pytest
from test_query_routing_preparation import routing as routing
from test_session_search import intake
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, auth, database, local_query_tasks
from fiber_bundle import document_creation_contract as contract
from fiber_bundle import document_creation_preparation as preparation
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.query_routing_config import parse


@pytest.fixture
def prepared(http, owner, config, routing):
    receipt = intake(http, owner)
    data = answer_tasks.AnswerInput(
        receipt["job_id"], "current", "What pressure?", "psi", submission_id="creation-input"
    )
    connection = database.connect(config.state_root)
    with database.locked(config):
        task = local_query_tasks.submit(config, connection, parse(routing), owner[0], data)
        plan = preparation.prepare(
            config, connection, owner[0], task["task_id"], "制作汇报\n保留原文及不确定性。", "md"
        )
        try:
            yield connection, plan, data
        finally:
            connection.close()


def test_offline_preparation_has_no_admission_calls_secrets_or_writes(config, owner, prepared):
    db, plan, _ = prepared
    before = list(db.iterdump())
    database_bytes = (config.state_root / "metadata.sqlite3").read_bytes()
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")),
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("subprocess.run", side_effect=AssertionError("process")),
    ):
        report = preparation.check(config, db, owner[0], plan)
        updated = preparation.amend(
            config, db, owner[0], plan, "增加结论。", expected_sha256=plan.sha256, phase="running"
        )
        result = preparation.artifact(
            config,
            db,
            owner[0],
            updated,
            b"synthetic candidate",
            "draft.md",
            expected_sha256=updated.sha256,
        )
    assert list(db.iterdump()) == before
    assert (config.state_root / "metadata.sqlite3").read_bytes() == database_bytes
    assert report["state"] == "prepared"
    assert report["input_coverage"] == "retrieved_excerpts"
    assert report["evidence_status"] == "evidence_only"
    assert report["input"]["quotations"][0]["quote"] == "100 psi session evidence"
    assert "RAW-EVENT-SECRET" not in canonical(report)
    assert "secret_file" not in canonical(report)
    assert "originals" not in canonical(report)
    for value in [report, result]:
        assert value["unverified_draft"] is True
        assert (
            value["format_verified"] is value["save_allowed"] is value["delivery_allowed"] is False
        )
    assert report["execution_allowed"] is report["semantic_verified"] is False
    assert result["file_exists_verified"] is result["uploadable_verified"] is False
    for table in ["model_calls", "local_query_calls", "execution_runs", "query_jobs"]:
        assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.parametrize(
    "phase,apply_at",
    [("prepared", "before_execution"), ("running", "next_version"), ("finished", "next_version")],
)
def test_additions_create_new_versions_without_mutating_original(
    config, owner, prepared, phase, apply_at
):
    db, plan, _ = prepared
    original = plan.value()
    updated = preparation.amend(
        config,
        db,
        owner[0],
        plan,
        "补充要求\n引用必须保持。",
        expected_sha256=plan.sha256,
        phase=phase,
    )
    assert updated.input_json == plan.input_json
    assert updated.requirements == (*plan.requirements, "补充要求\n引用必须保持。")
    assert updated.revision == 2 and updated.parent_sha256 == plan.sha256
    assert updated.apply_at == apply_at
    assert updated.sha256 != plan.sha256 and plan.value() == original
    with pytest.raises(FrozenInstanceError):
        plan.revision = 2
    original["input"]["quotations"][0]["quote"] = "tampered external dictionary"
    assert plan.value()["input"]["quotations"][0]["quote"] == "100 psi session evidence"


def test_stale_plan_digest_and_unknown_phase_refused(config, owner, prepared):
    db, plan, _ = prepared
    with pytest.raises(InvalidInput, match="plan_conflict"):
        preparation.amend(
            config, db, owner[0], plan, "new", expected_sha256="0" * 64, phase="prepared"
        )
    with pytest.raises(InvalidInput, match="phase_invalid"):
        preparation.amend(
            config, db, owner[0], plan, "new", expected_sha256=plan.sha256, phase="safe-append"
        )


@pytest.mark.parametrize("change", ["revoke", "grant", "session", "source"])
def test_authority_or_source_change_refuses_check_revision_and_artifact(
    config, owner, prepared, change
):
    db, plan, _ = prepared
    original = plan.sha256
    if change == "revoke":
        auth.revoke_client(db, owner[0])
    elif change == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif change == "session":
        db.execute("DELETE FROM session_refs")
    else:
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    calls = [
        lambda: preparation.check(config, db, owner[0], plan),
        lambda: preparation.amend(
            config, db, owner[0], plan, "new", expected_sha256=original, phase="running"
        ),
        lambda: preparation.artifact(
            config, db, owner[0], plan, b"candidate", "draft.md", expected_sha256=original
        ),
    ]
    for call in calls:
        with pytest.raises((auth.Denied, IntegrityError)):
            call()
    assert plan.sha256 == original


def test_owner_and_explicit_task_binding_cannot_be_replaced(config, owner, prepared, routing):
    db, plan, data = prepared
    other = auth.create_client(db, "ungranted", False)["id"]
    with pytest.raises(auth.Denied):
        preparation.check(config, db, other, plan)
    with pytest.raises(auth.Denied):
        preparation.prepare(config, db, other, plan.query_task_id, "new", "md")
    second = local_query_tasks.submit(
        config, db, parse(routing), owner[0], replace(data, submission_id="second-input")
    )
    forged = replace(plan, query_task_id=second["task_id"])
    with pytest.raises(InvalidInput):
        preparation.check(config, db, owner[0], forged)
    value = json.loads(plan.input_json)
    value["query_task_id"] = second["task_id"]
    forged = replace(forged, input_json=canonical(value))
    with pytest.raises(IntegrityError, match="input_changed"):
        preparation.check(config, db, owner[0], forged)


@pytest.mark.parametrize("state", ["running", "failed", "cancelled", "interrupted"])
def test_unready_query_task_is_not_an_input_authorization(config, owner, prepared, state):
    db, plan, _ = prepared
    db.execute("UPDATE local_query_tasks SET state=? WHERE id=?", (state, plan.query_task_id))
    with pytest.raises(InvalidInput, match="input_task_not_ready"):
        preparation.check(config, db, owner[0], plan)


def test_artifact_descriptor_is_version_bound_and_never_a_format_or_delivery_proof(
    config, owner, prepared
):
    db, plan, _ = prepared
    data = b"plain bytes are not a validated document format"
    value = preparation.artifact(
        config, db, owner[0], plan, data, "draft.md", expected_sha256=plan.sha256
    )
    assert value["plan_sha256"] == plan.sha256
    assert value["input_sha256"] == digest(plan.input_json.encode())
    assert value["artifact"]["sha256"] == digest(data)
    assert value["artifact"]["byte_count"] == len(data)
    assert value["revision"] == 1 and value["parent_plan_sha256"] is None
    updated = preparation.amend(
        config, db, owner[0], plan, "new", expected_sha256=plan.sha256, phase="finished"
    )
    with pytest.raises(InvalidInput, match="plan_conflict"):
        preparation.artifact(
            config, db, owner[0], updated, data, "draft.md", expected_sha256=plan.sha256
        )
    second = preparation.artifact(
        config, db, owner[0], updated, data, "draft.md", expected_sha256=updated.sha256
    )
    assert second["parent_plan_sha256"] == plan.sha256 and second["revision"] == 2


@pytest.mark.parametrize(
    "data,filename",
    [
        (b"", "draft.md"),
        (bytearray(b"x"), "draft.md"),
        (b"x", "../draft.md"),
        (b"x", "/tmp/draft.md"),
        (b"x", "draft.docx"),
        (b"x" * (contract.MAX_ARTIFACT_BYTES + 1), "draft.md"),
    ],
)
def test_invalid_artifact_candidate_rejected(prepared, data, filename):
    _, plan, _ = prepared
    with pytest.raises(InvalidInput):
        contract.artifact(plan, data, filename, expected_sha256=plan.sha256)


@pytest.mark.parametrize(
    "requirement", ["", "  ", "bad\0text", "bad\rtext", "\ud800", "中" * 6000, 1, True]
)
def test_invalid_requirements_rejected(prepared, requirement):
    _, plan, _ = prepared
    with pytest.raises(InvalidInput):
        contract.amend(plan, requirement, expected_sha256=plan.sha256, phase="prepared")


@pytest.mark.parametrize(
    "format", ["", "../md", "application/pdf", "DOCX", "https://provider", True]
)
def test_requested_format_is_a_bounded_label_not_a_capability(prepared, format):
    _, plan, _ = prepared
    with pytest.raises(InvalidInput):
        replace(plan, requested_format=format).value()
    assert replace(plan, requested_format="future-format").value()["format_verified"] is False


def test_revision_and_cumulative_instruction_limits(prepared):
    _, plan, _ = prepared
    large = replace(plan, requirements=("x" * contract.MAX_REQUIREMENT_BYTES,))
    with pytest.raises(InvalidInput):
        contract.amend(large, "x", expected_sha256=large.sha256, phase="prepared")
    current = plan
    for _ in range(contract.MAX_REVISIONS - 1):
        current = contract.amend(current, "x", expected_sha256=current.sha256, phase="running")
    with pytest.raises(InvalidInput):
        contract.amend(current, "x", expected_sha256=current.sha256, phase="running")


def test_empty_evidence_is_explicit_and_does_not_grant_execution(config, owner, prepared, routing):
    db, _, data = prepared
    task = local_query_tasks.submit(
        config,
        db,
        parse(routing),
        owner[0],
        replace(data, retrieval_query="no-such-evidence", submission_id="empty-input"),
    )
    plan = preparation.prepare(config, db, owner[0], task["task_id"], "说明证据不足", "md")
    value = preparation.check(config, db, owner[0], plan)
    assert value["input"]["sources"] == value["input"]["quotations"] == []
    assert (
        value["evidence_status"] == "insufficient_evidence" and value["execution_allowed"] is False
    )


@pytest.mark.parametrize("change", ["quote", "scope", "version", "binding", "extra"])
def test_forged_input_cannot_pass_current_host_binding(config, owner, prepared, change):
    db, plan, _ = prepared
    value = json.loads(plan.input_json)
    if change == "quote":
        value["quotations"][0]["quote"] = "forged source"
    elif change == "scope":
        value["vault_ids"].append(owner[2]["supplier"])
    elif change == "version":
        value["source_version_id"] = "forged version"
    elif change == "binding":
        value["binding_sha256"] = "0" * 64
    else:
        value["execute"] = True
    with pytest.raises((InvalidInput, IntegrityError)):
        preparation.check(config, db, owner[0], replace(plan, input_json=canonical(value)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("vault_ids", []),
        ("vault_ids", [True]),
        ("binding_sha256", "bad"),
        ("source_version_id", []),
        ("sources", True),
        ("quotations", []),
        ("quotations", [{"citation": "[1]", "quote": "", "extra": True}]),
    ],
)
def test_malformed_input_contract_rejected(prepared, field, value):
    _, plan, _ = prepared
    data = json.loads(plan.input_json)
    data[field] = value
    with pytest.raises(InvalidInput):
        replace(plan, input_json=canonical(data)).value()


@pytest.mark.parametrize(
    "change",
    [
        {"revision": True},
        {"revision": 2},
        {"parent_sha256": "0" * 64},
        {"apply_at": True},
        {"input_json": "{}"},
        {"input_json": "x" * (contract.MAX_INPUT_BYTES + 1)},
    ],
)
def test_malformed_plan_metadata_rejected(prepared, change):
    _, plan, _ = prepared
    with pytest.raises(InvalidInput):
        replace(plan, **change).value()
