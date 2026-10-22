import json

import pytest

from fiber_bundle import auth, database
from fiber_bundle.config import InvalidInput
from fiber_bundle.suppliers import commands, ingestion, read
from fiber_bundle.suppliers import store as ledger
from fiber_bundle.suppliers.contracts import Correction, ManualQuote, Offer, Policy


def scoped(config):
    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "synthetic supplier operator", False)
        vault = db.execute("select id from vaults where name='supplier'").fetchone()[0]
        auth.set_grant(db, client["id"], vault, query=True, original=True, add=True)
    return client["id"], vault


def offer(**kw):
    return Offer(
        supplier_name="Synthetic supplier",
        product_name="Diamond Alpha",
        model="Alpha",
        amount="590",
        currency="CNY",
        unit="100g",
        evidence_quote="Synthetic supplier Diamond Alpha 590 CNY/100g",
        **kw,
    )


def test_manual_retains_exact_source_and_is_idempotent(config, store):
    cid, vid = scoped(config)
    request = ManualQuote(client_id=cid, vault_id=vid, request_id="one", offer=offer())
    with database.locked(config), database.connect(config.state_root) as db:
        first = ingestion.manual(config, db, request)
        assert ingestion.manual(config, db, request) == first
        raw, name, mime = read.original(config, db, cid, vid, first["source_id"])
        assert json.loads(raw) == request.model_dump()
        assert mime == "application/json" and name == "manual-quote.json"
        assert len(ledger.rows(config, vid, "quote")) == 1
        q = ledger.rows(config, vid, "quote")[0]
        assert not q["knowledge_verified"] and q["actor"] == "user_entry"
        assert (
            read.evidence(config, db, cid, vid, first["source_id"])["original_state"] == "retained"
        )
        with pytest.raises(InvalidInput, match="supplier_request_conflict"):
            ingestion.manual(
                config, db, request.model_copy(update={"offer": offer(quoted_at="2026-10-21")})
            )
    assert not list((config.project_root / "test vault/temp_vault2").rglob("*.md"))


def test_manual_corrections_have_their_own_retained_provenance(config, store):
    cid, vid = scoped(config)
    with database.locked(config), database.connect(config.state_root) as db:
        ingestion.manual(
            config, db, ManualQuote(client_id=cid, vault_id=vid, request_id="one", offer=offer())
        )
        q = ledger.rows(config, vid, "quote")[0]
        request = Correction(
            client_id=cid,
            vault_id=vid,
            request_id="fix",
            kind="quote",
            identity=q["id"],
            expected_sha256=commands.version(q),
            fields={"amount": "600"},
            reason="User clarified the quote",
        )
        c = commands.correct(config, db, request)
        assert commands.correct(config, db, request) == c
        corrected = ledger.rows(config, vid, "quote")[0]
        assert corrected["amount"] == "590" and corrected["overrides"]["amount"] == "600"
        assert corrected["correction_ids"] == [c["id"]]
        assert (
            db.execute("select state from receipts where job_id=?", (c["receipt_id"],)).fetchone()[
                0
            ]
            == "stored"
        )
        with pytest.raises(InvalidInput, match="supplier_record_changed"):
            commands.correct(config, db, request.model_copy(update={"request_id": "new"}))


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_quote": "A made-up row"},
        {"model": "Beta"},
        {"amount": "59"},
        {"tax": "included"},
        {"specs": {"particle_size": "999 um"}},
        {"unit": "kg"},
        {"currency": "USD"},
    ],
)
def test_extraction_rejects_ungrounded_fields_and_cross_row_prices(change):
    text = offer().evidence_quote + "\nSynthetic supplier Diamond Beta 999 CNY/kg"
    bad = {**offer().model_dump(), **change}
    with pytest.raises(InvalidInput):
        ingestion.validate_extraction(json.dumps({"offers": [bad], "warnings": []}), text)


def test_grounded_extraction_and_duplicate_keys():
    result = ingestion.validate_extraction(
        json.dumps({"offers": [offer().model_dump()]}), offer().evidence_quote
    )
    assert result.offers[0].model == "Alpha"
    with pytest.raises(InvalidInput):
        ingestion.validate_extraction('{"offers":[],"offers":[]}', offer().evidence_quote)


def test_http_scope_csrf_policy_and_original_download(config, http):
    cid, vid = scoped(config)
    body = {"client_id": cid, "vault_id": vid}
    assert http.get("/admin/suppliers", params=body).json()["profile"]["revision"] == 0
    save = {
        **body,
        "expected_revision": 0,
        "settings": Policy(enabled=True, cloud_allowed=True, review_mode="automatic").model_dump(),
    }
    assert http.post("/admin/suppliers/policy", json=save).status_code == 200
    assert http.post("/admin/suppliers/policy", json=save).status_code == 422
    request = {**body, "request_id": "http-manual", "offer": offer().model_dump()}
    saved = http.post("/admin/suppliers/manual", json=request)
    assert saved.status_code == 200, saved.text
    source = saved.json()["source_id"]
    downloaded = http.get("/admin/suppliers/original/" + source, params=body)
    assert (
        downloaded.status_code == 200
        and json.loads(downloaded.content) == ManualQuote.model_validate(request).model_dump()
    )
    with database.connect(config.state_root) as db:
        auth.set_grant(db, cid, vid, query=True, original=False, add=True)
    assert http.get("/admin/suppliers/original/" + source, params=body).status_code == 404
    http.headers.pop("X-CSRF-Token")
    assert (
        http.post("/admin/suppliers/manual", json={**request, "request_id": "csrf"}).status_code
        == 403
    )
    http.cookies.clear()
    assert http.get("/admin/suppliers", params=body).status_code == 401


def test_runtime_has_no_opt_in_no_model_and_no_sidecar(config, store, monkeypatch):
    from fiber_bundle.suppliers import model
    from fiber_bundle.suppliers.runtime import Runtime

    monkeypatch.setattr(model, "run", lambda *a: pytest.fail("Idle polling called a model"))
    Runtime(config).tick()
    assert not (config.state_root / ledger.NAME).exists()


def test_separate_company_header_and_multi_model_row_are_grounded_independently():
    text = "Synthetic supplier\nDiamond Alpha 590 CNY/100g\nDiamond Beta 999 CNY/kg 不含税"
    data = {
        **offer().model_dump(),
        "company_evidence": "Synthetic supplier",
        "evidence_quote": "Diamond Alpha 590 CNY/100g",
    }
    assert (
        ingestion.validate_extraction(json.dumps({"offers": [data]}), text).offers[0].amount
        == "590"
    )
    data.update(
        product_name="Diamond Beta",
        model="Beta",
        amount="999",
        unit="kg",
        evidence_quote="Diamond Beta 999 CNY/kg 不含税",
        tax="included",
    )
    with pytest.raises(InvalidInput, match="supplier_terms_evidence_missing"):
        ingestion.validate_extraction(json.dumps({"offers": [data]}), text)
