"""Processing policy boundaries, explicit migration and frozen task behaviour."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from document_support import candidate
from test_document_tasks import document_scope as document_scope
from test_execution import policy as policy
from test_source_processing import WORKERS, complete, prepare

from fiber_bundle import auth, database, source_processing_policy, source_task_policy, source_tasks
from fiber_bundle import source_processing_contract as contract
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest


def plan(value=None, **kwargs):
    return contract.freeze(
        value or contract.defaults(),
        format="pdf",
        source_sha256="a" * 64,
        source_bytes=20,
        **kwargs,
    )


@pytest.mark.parametrize("key", list(contract.DEFAULT_LIMITS))
@pytest.mark.parametrize("bad", [0, True, 1.5, "1"])
def test_reject_wrong_limit_types(key, bad):
    value = contract.defaults()
    value["limits"][key] = bad
    with pytest.raises(InvalidInput):
        contract.normalize(value)


@pytest.mark.parametrize("key", list(contract.DEFAULT_LIMITS))
def test_capacity_ceiling_is_not_silently_clamped(key):
    value = contract.defaults()
    value["limits"][key] = contract.CAPACITY_LIMITS[key] + 1
    with pytest.raises(InvalidInput, match="outside_capacity"):
        contract.normalize(value)


def test_legacy_limits_and_exact_archive_source_boundary_without_model():
    value = contract.normalize(WORKERS)
    assert value["revision"] == 0 and value["limits"] == contract.DEFAULT_LIMITS
    archived = contract.freeze(
        contract.defaults(),
        format="doc",
        source_sha256="a" * 64,
        source_bytes=contract.DEFAULT_LIMITS["source_bytes"],
        action="archive",
    )
    assert contract.capability(archived) == {
        "binding_ready": True,
        "model_required": False,
        "reason": "archive_only",
    }
    with pytest.raises(InvalidInput, match="source_limit"):
        contract.freeze(
            contract.defaults(),
            format="pdf",
            source_sha256="a" * 64,
            source_bytes=contract.DEFAULT_LIMITS["source_bytes"] + 1,
        )


@pytest.mark.parametrize(
    "engine,format,profile,reason",
    [
        ("ordinary", "pdf", "native", "document_worker_not_configured"),
        ("fake-expert", "pdf", "native", "full_fake_expert_not_integrated"),
        ("ordinary", "doc", "native", "doc_converter_not_integrated"),
        ("ordinary", "markdown", "native", "document_worker_not_configured"),
        ("ordinary", "image", "ocr", "ocr_profile_required"),
    ],
)
def test_no_fallback_or_fake_readiness(engine, format, profile, reason):
    value = contract.freeze(
        contract.defaults(),
        format=format,
        profile=profile,
        engine=engine,
        source_sha256="a" * 64,
        source_bytes=1,
    )
    result = contract.capability(value)
    assert result["reason"] == reason and result["binding_ready"] is False
    assert result["model_required"] == (engine == "fake-expert")
    assert result["runtime_verified"] is False


def test_overrides_and_deep_frozen_copy():
    value = contract.defaults()
    frozen = plan(value, overrides={"pdf_pages": 3})
    value["limits"]["pdf_pages"] = 1
    assert frozen["limits"]["pdf_pages"] == 3
    restored = contract.validate_plan(frozen)
    restored["limits"]["pdf_pages"] = 2
    assert frozen["limits"]["pdf_pages"] == 3
    with pytest.raises(InvalidInput, match="exceeds_policy"):
        plan(value, overrides={"pdf_pages": 2})
    with pytest.raises(InvalidInput, match="overrides_invalid"):
        plan(overrides={"unknown": 1})


@pytest.mark.parametrize(
    "field,bad",
    [
        ("publication_allowed", 0),
        ("model_required", 0),
        ("schema", "x"),
        ("source_bytes", True),
        ("plan_sha256", "b" * 64),
        ("extra", 1),
    ],
)
def test_frozen_contract_strictness(field, bad):
    value = plan()
    value[field] = bad
    with pytest.raises(InvalidInput, match="frozen_plan"):
        contract.validate_plan(value)


def test_utf8_and_metadata_are_counted_in_output_budgets():
    frozen = plan(overrides={"markdown_bytes": 8, "markdown_total_bytes": 10})
    contract.check_markdown(frozen, {"note.md": "标题\n".encode()})
    with pytest.raises(InvalidInput, match="file_limit"):
        contract.check_markdown(frozen, {"note.md": "标题标题".encode()})
    with pytest.raises(InvalidInput, match="total_limit"):
        contract.check_markdown(frozen, {"a.md": b"123456", "b.md": b"123456"})


def test_page_text_result_and_source_limits():
    source = b"%PDF-synthetic"
    value = candidate(source, "中文")
    frozen = contract.freeze(
        contract.defaults(),
        format="pdf",
        source_sha256=digest(source),
        source_bytes=len(source),
        overrides={"text_bytes": 6},
    )
    contract.check_result(frozen, value)
    changed = copy.deepcopy(value)
    changed["pages"][0]["text"] += "中"
    with pytest.raises(InvalidInput, match="text_limit"):
        contract.check_result(frozen, changed)
    with pytest.raises(InvalidInput, match="source_changed"):
        contract.check_result(frozen, {**value, "source_bytes": 1})
    result_limited = contract.freeze(
        contract.defaults(),
        format="pdf",
        source_sha256=digest(source),
        source_bytes=len(source),
        overrides={"result_bytes": 1},
    )
    with pytest.raises(InvalidInput, match="result_limit"):
        contract.check_result(result_limited, value)
    page_limited = contract.freeze(
        contract.defaults(),
        format="pdf",
        source_sha256=digest(source),
        source_bytes=len(source),
        overrides={"pdf_pages": 1},
    )
    with pytest.raises(InvalidInput, match="page_limit"):
        contract.check_result(page_limited, {**value, "pages": value["pages"] * 2})


def test_legacy_config_is_read_only_and_save_migrates_without_model(config, store):
    directory = config.state_root / "configs"
    directory.mkdir()
    file = directory / "source-processing.json"
    original = canonical(WORKERS).encode()
    file.write_bytes(original)
    before = (config.state_root / "metadata.sqlite3").read_bytes()
    status = source_processing_policy.inspect(config, None)
    assert file.read_bytes() == original and status["effective_policy"]["revision"] == 0
    assert status["policy_configured"] and not status["model_configured"]
    assert status["capacity_scope"] == "file_native_pdf_structured_parts"
    assert status["structured_parts_ready"] is True
    with database.locked(config):
        saved = source_processing_policy.save(config, None, WORKERS, status["configuration_sha256"])
    assert saved["value"]["schema"] == 2 and saved["value"]["revision"] == 1
    assert file.stat().st_mode & 0o777 == 0o600
    assert (config.state_root / "metadata.sqlite3").read_bytes() == before
    assert (
        source_processing_policy.inspect(config, None)["configuration_sha256"]
        == saved["configuration_sha256"]
    )


def test_legacy_ui_cannot_erase_new_limits_and_bad_cas_cannot_write(config, store):
    value = contract.defaults()
    value["limits"]["pdf_pages"] = 7
    first = source_processing_policy.save(config, None, value, None)
    second = source_processing_policy.save(config, None, WORKERS, first["configuration_sha256"])
    assert second["value"]["limits"]["pdf_pages"] == 7 and second["value"]["revision"] == 2
    file = config.state_root / "configs/source-processing.json"
    before = file.read_bytes()
    with pytest.raises(InvalidInput, match="changed"):
        source_processing_policy.save(config, None, WORKERS, first["configuration_sha256"])
    wrong_revision = {**second["value"], "revision": 10}
    with pytest.raises(InvalidInput, match="revision_conflict"):
        source_processing_policy.save(config, None, wrong_revision, second["configuration_sha256"])
    assert file.read_bytes() == before


def test_two_locked_config_writers_accept_only_one_head(config, store):
    first = source_processing_policy.save(config, None, contract.defaults(), None)

    def save(number):
        with database.locked(config):
            try:
                value = {**first["value"], "revision": 2, "timeout_seconds": number}
                source_processing_policy.save(config, None, value, first["configuration_sha256"])
                return "saved"
            except InvalidInput:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [30, 40])) == ["conflict", "saved"]


def test_private_save_failure_preserves_old_policy(config, store, monkeypatch):
    first = source_processing_policy.save(config, None, WORKERS, None)
    file = config.state_root / "configs/source-processing.json"
    before = file.read_bytes()

    def fail(*args):
        raise OSError("synthetic replacement failure")

    monkeypatch.setattr(source_processing_policy.query_policy_store, "write_private", fail)
    with pytest.raises(OSError):
        source_processing_policy.save(config, None, WORKERS, first["configuration_sha256"])
    assert file.read_bytes() == before


def test_duplicate_or_unbounded_config_rejected(config, store):
    directory = config.state_root / "configs"
    directory.mkdir()
    file = directory / "source-processing.json"
    file.write_text('{"timeout_seconds":60,"timeout_seconds":61}')
    with pytest.raises(InvalidInput, match="duplicate"):
        source_processing_policy.inspect(config, None)
    file.write_bytes(b"x" * 16385)
    with pytest.raises(InvalidInput):
        source_processing_policy.inspect(config, None)


def test_task_policy_fixed_across_settings_and_legacy_rows_untouched(
    config, policy, document_scope
):
    cid, vid, receipt, _, _ = document_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    request, rid = prepare(config, db, policy, cid, vid, receipt["job_id"])
    old = source_task_policy.frozen(db, rid)
    assert old["limits"] == contract.DEFAULT_LIMITS
    settings = source_processing_policy.inspect(config, policy)
    new_value = {
        **settings["value"],
        "revision": settings["value"]["revision"] + 1,
        "limits": {**contract.DEFAULT_LIMITS, "pdf_pages": 1},
    }
    source_processing_policy.save(config, policy, new_value, settings["configuration_sha256"])
    assert source_task_policy.frozen(db, rid) == old
    assert source_tasks.submit(config, db, None, request)["run_id"] == rid
    # A historical row is read from its immutable worker columns, never backfilled.
    db.execute(
        "UPDATE source_task_bindings SET request_json=? WHERE run_id=?", (canonical(request), rid)
    )
    before = tuple(
        db.execute("SELECT * FROM source_task_bindings WHERE run_id=?", (rid,)).fetchone()
    )
    legacy = source_task_policy.frozen(db, rid)
    assert legacy["policy"]["revision"] == 0 and legacy["limits"] == contract.DEFAULT_LIMITS
    assert (
        tuple(db.execute("SELECT * FROM source_task_bindings WHERE run_id=?", (rid,)).fetchone())
        == before
    )
    db.close()


def test_frozen_plan_checked_again_on_evidence_and_publish(config, policy, document_scope):
    from fiber_bundle import document_evidence, document_publication, document_review

    cid, vid, receipt, _, _ = document_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    _, initial = prepare(config, db, policy, cid, vid, receipt["job_id"])
    settings = source_processing_policy.inspect(config, policy)
    value = {
        **settings["value"],
        "revision": 2,
        "limits": {**contract.DEFAULT_LIMITS, "markdown_bytes": 1},
    }
    updated = source_processing_policy.save(config, policy, value, settings["configuration_sha256"])
    req = source_task_policy.submission(
        db.execute(
            "SELECT request_json FROM source_task_bindings WHERE run_id=?", (initial,)
        ).fetchone()[0]
    )
    req.update(submission_id="smaller-output", configuration_sha256=updated["configuration_sha256"])
    rid = source_tasks.submit(config, db, policy, req)["run_id"]
    complete(config, db, policy, rid)
    _, result = document_evidence.load(config, db, cid, rid)
    document_review.record(
        db,
        rid,
        result,
        dict(
            source_sha256=result["source_sha256"],
            result_sha256=result["result_sha256"],
            source_reviewed=True,
            visual_reviewed=True,
            judgments=[
                dict(unit_id=u["unit_id"], verdict="accept", reason="synthetic")
                for u in document_review.units(result)
            ],
        ),
    )
    with pytest.raises(InvalidInput, match="markdown_file_limit"):
        document_publication.publish_locked(config, db, rid)
    assert (
        db.execute("SELECT COUNT(*) FROM document_publications WHERE run_id=?", (rid,)).fetchone()[
            0
        ]
        == 0
    )
    assert db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    raw = json.loads(
        db.execute(
            "SELECT request_json FROM source_task_bindings WHERE run_id=?", (rid,)
        ).fetchone()[0]
    )
    raw["processing"]["publication_allowed"] = True
    db.execute(
        "UPDATE source_task_bindings SET request_json=? WHERE run_id=?", (canonical(raw), rid)
    )
    with pytest.raises(InvalidInput, match="frozen_plan"):
        document_evidence.load(config, db, cid, rid)
    db.close()


def test_real_http_policy_saves_are_not_nested_locked_or_model_gated(http, config):
    response = http.put(
        "/admin/source-policy", json={"expected_sha256": None, "value": contract.defaults()}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["policy_configured"] and not data["model_configured"] and not data["configured"]
    assert (
        http.get("/admin/source-policy").json()["configuration_sha256"]
        == data["configuration_sha256"]
    )
    denied = http.put(
        "/admin/source-policy", json={"expected_sha256": None, "value": contract.defaults()}
    )
    assert denied.status_code == 422


def test_lower_source_limit_rejects_before_original_read_or_new_rows(
    config, policy, document_scope, monkeypatch
):
    cid, vid, receipt, _, _ = document_scope
    db = database.connect(config.state_root)
    auth.set_grant(db, cid, vid, query=True, original=True, add=True)
    request, _ = prepare(config, db, policy, cid, vid, receipt["job_id"])
    settings = source_processing_policy.inspect(config, policy)
    updated = source_processing_policy.save(
        config,
        policy,
        {
            **settings["value"],
            "revision": 2,
            "limits": {**contract.DEFAULT_LIMITS, "source_bytes": 1},
        },
        settings["configuration_sha256"],
    )
    request.update(submission_id="limited", configuration_sha256=updated["configuration_sha256"])
    before = list(db.iterdump())

    def forbid(*args, **kwargs):
        pytest.fail("over-limit original was read")

    monkeypatch.setattr(source_tasks, "original", forbid)
    with pytest.raises(InvalidInput, match="source_limit"):
        source_tasks.submit(config, db, policy, request)
    assert list(db.iterdump()) == before
    db.close()


def test_minimum_values_and_malformed_policy_are_not_unlimited():
    value = contract.defaults()
    value["limits"] = {k: 1 for k in contract.DEFAULT_LIMITS}
    assert contract.normalize(value)["limits"] == value["limits"]
    for changed in [
        {**value, "schema": True},
        {**value, "revision": True},
        {**value, "revision": 0},
        {**value, "oversize_action": "truncate"},
        {**value, "timeout_seconds": True},
        {**value, "document_image": "latest"},
        {**value, "limits": {}},
        {**value, "extra": True},
    ]:
        with pytest.raises(InvalidInput):
            contract.normalize(changed)


def test_corrupt_or_downgraded_task_envelope_is_not_legacy():
    for raw in [
        "{}",
        '{"request":{},"processing":{}}',
        '{"schema":"source-submission/v2","request":{},"processing":{}}',
        '{"schema":1,"schema":2}',
    ]:
        with pytest.raises(IntegrityError):
            source_task_policy.submission(raw)


def test_image_budget_counts_actual_dimensions():
    source = b"image"
    value = contract.freeze(
        contract.defaults(),
        format="image",
        profile="ocr",
        source_sha256=digest(source),
        source_bytes=len(source),
        overrides={"image_pixels": 6, "image_dimension": 3},
    )
    result = {
        "source_sha256": digest(source),
        "source_bytes": len(source),
        "segments": [{"text": "", "locator": {"width": 3, "height": 2}}],
    }
    contract.check_result(value, result)
    for width, height in [(3, 3), (4, 1)]:
        changed = copy.deepcopy(result)
        changed["segments"][0]["locator"].update(width=width, height=height)
        with pytest.raises(InvalidInput, match="image_limit"):
            contract.check_result(value, changed)


def test_image_pixel_limit_counts_all_frames_without_lowering_pdf_legacy_limit():
    value = contract.defaults()
    value["limits"]["image_pixels"] = 60_000
    frozen = contract.freeze(
        value,
        format="image",
        source_sha256="a" * 64,
        source_bytes=20,
        action="archive",
        engine="ordinary",
        overrides={},
    )
    result = {
        "source_sha256": "a" * 64,
        "source_bytes": 20,
        "segments": [
            {"text": "first", "locator": {"width": 200, "height": 200}},
            {"text": "second", "locator": {"width": 200, "height": 200}},
        ],
    }
    with pytest.raises(InvalidInput, match="processing_image_limit"):
        contract.check_result(frozen, result)
    result["segments"] = result["segments"][:1]
    contract.check_result(frozen, result)
