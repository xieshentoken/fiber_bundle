import pytest
from pydantic import ValidationError
from test_ingestion import offer, scoped

from fiber_bundle import auth, backup_bundle, backup_restore, database
from fiber_bundle.config import InvalidInput, load_config
from fiber_bundle.suppliers import ingestion, query, read, service
from fiber_bundle.suppliers import store as ledger
from fiber_bundle.suppliers.contracts import (
    Compare,
    Condition,
    Evidence,
    ManualQuote,
    Offer,
    Policy,
    SavePolicy,
)


def save(config, db, cid, vid, event, **kw):
    values = {**offer().model_dump(), **kw}
    return ingestion.manual(
        config,
        db,
        ManualQuote(
            client_id=cid, vault_id=vid, request_id=event, offer=Offer.model_validate(values)
        ),
    )


def test_full_exact_query_units_specs_dates_and_pagination(config, store):
    cid, vid = scoped(config)
    with database.locked(config), database.connect(config.state_root) as db:
        for i in range(32):
            save(
                config,
                db,
                cid,
                vid,
                str(i),
                amount=str(590 + i),
                specs={"coating": "1000 nm"},
                quoted_at="2026-10-01",
                valid_until="2026-12-01",
            )
        save(config, db, cid, vid, "wrong-unit", unit="piece")
        save(config, db, cid, vid, "wrong-model", model="Beta", specs={"coating": "1000 nm"})
        conditions = Condition(
            vault_id=vid,
            model="Alpha",
            currency="CNY",
            price_unit="kg",
            price_max="6210",
            valid_on="2026-11-01",
            specs=[{"field": "coating", "minimum": "0.9", "maximum": "1.1", "unit": "um"}],
            limit=10,
        )
        first = query.select(config, db, cid, conditions)
        assert first["total"] == 32 and first["next_offset"] == 10 and len(first["items"]) == 10
        assert first["coverage"]["whole_vault_complete"] is False
        seen = [r["id"] for r in first["items"]]
        while first["next_offset"] is not None:
            first = query.select(
                config,
                db,
                cid,
                conditions.model_copy(
                    update={"offset": first["next_offset"], "expected_snapshot": first["snapshot"]}
                ),
            )
            seen += [r["id"] for r in first["items"]]
        assert len(seen) == len(set(seen)) == 32
        assert query.metric("60/70", "um") is None
        assert query.metric("1000 nm", "um") == 1
        assert (
            query.select(config, db, cid, Condition(vault_id=vid, kind="products"))["total"] == 34
        )
        assert (
            query.select(config, db, cid, Condition(vault_id=vid, kind="suppliers"))["total"] == 1
        )
        save(config, db, cid, vid, "later")
        with pytest.raises(InvalidInput, match="snapshot_changed"):
            query.select(
                config,
                db,
                cid,
                conditions.model_copy(update={"expected_snapshot": first["snapshot"]}),
            )


def test_unknowns_never_become_current_taxfree_or_comparable(config, store):
    cid, vid = scoped(config)
    with database.locked(config), database.connect(config.state_root) as db:
        save(config, db, cid, vid, "a")
        save(config, db, cid, vid, "b", amount="58", unit="g")
        result = query.select(config, db, cid, Condition(vault_id=vid, valid_on="2026-10-21"))
        assert not result["items"] and result["excluded"]["validity_unknown"] == 2
        result = query.select(config, db, cid, Condition(vault_id=vid, tax="included"))
        assert not result["items"] and result["excluded"]["tax_unknown_or_other"] == 2
        ids = [r["id"] for r in ledger.rows(config, vid, "quote")]
        comparison = query.compare(config, db, cid, Compare(vault_id=vid, quote_ids=ids))
        assert len(comparison["groups"]) == 2 and not any(
            g["comparable"] for g in comparison["groups"]
        )
        for event, amount, unit in [("c", "590", "100g"), ("d", "5800", "kg")]:
            save(
                config,
                db,
                cid,
                vid,
                event,
                amount=amount,
                unit=unit,
                quoted_at="2026-10-01",
                valid_until="2026-12-01",
                tax="included",
                freight="excluded",
            )
        newids = [r["id"] for r in ledger.rows(config, vid, "quote") if r["id"] not in ids]
        comparison = query.compare(config, db, cid, Compare(vault_id=vid, quote_ids=newids))
        assert len(comparison["groups"]) == 1 and comparison["groups"][0]["comparable"]
        assert [r["normalized_price"] for r in comparison["groups"][0]["items"]] == ["5800", "5900"]


@pytest.mark.parametrize(
    "change",
    [
        {"price_max": "2"},
        {"price_min": "3", "price_max": "2", "currency": "CNY", "price_unit": "kg"},
        {"quoted_after": "2026-10-21", "quoted_before": "2026-01-01"},
        {"valid_on": "20261021"},
    ],
)
def test_invalid_conditions_rejected(change):
    with pytest.raises(ValidationError):
        Condition(vault_id="synthetic", **change)


def test_scope_evidence_and_preview_never_writes_notes(config, store):
    cid, vid = scoped(config)
    with database.locked(config), database.connect(config.state_root) as db:
        saved = save(config, db, cid, vid, "a")
        source = ledger.rows(config, vid, "source")[0]
        data = Evidence(
            vault_id=vid, source_id=saved["source_id"], expected_sha256=source["sha256"], length=20
        )
        result = read.bounded_evidence(config, db, cid, data)
        assert len(result["text"]) == 20 and result["next_offset"] == 20
        other = auth.create_client(db, "query-only", False)
        auth.set_grant(db, other["id"], vid, query=True, original=False, add=False)
        assert query.select(config, db, other["id"], Condition(vault_id=vid))["total"] == 1
        with pytest.raises(auth.Denied):
            read.original(config, db, other["id"], vid, source["id"])
        another = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
        with pytest.raises(auth.Denied):
            query.select(config, db, other["id"], Condition(vault_id=another))
        preview = query.classifications(config, db, cid, vid)
        assert not preview["vault_files_written"] and not preview["archive_executed"]
    assert not list((config.project_root / "test vault/temp_vault2").rglob("*.md"))


@pytest.mark.parametrize("incremental", [False, True])
def test_supplier_sidecar_and_originals_restore_with_processing_paused(
    config, store, tmp_path, incremental
):
    cid, vid = scoped(config)
    with database.locked(config), database.connect(config.state_root) as db:
        save(config, db, cid, vid, "a")
        service.save_policy(
            config,
            db,
            SavePolicy(
                client_id=cid,
                vault_id=vid,
                expected_revision=0,
                settings=Policy(enabled=True, cloud_allowed=True),
            ),
        )
    if incremental:
        from fiber_bundle.backup_incremental import snapshot

        snapshot.create(config, tmp_path / "bundle", "a1" * 32)
        password = "a1" * 32
    else:
        password = "synthetic-password"
        backup_bundle.create(config, tmp_path / "bundle", password)
    result = backup_restore.restore(tmp_path / "bundle", tmp_path / "restored", password)
    assert result["original_hashes_verified"] and result["quarantine"]["supplier-intelligence"] == 1
    restored = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
    assert ledger.rows(restored, vid, "quote") == ledger.rows(config, vid, "quote")
    assert not ledger.profile(restored, vid)["settings"]["enabled"]
    assert ledger.profile(config, vid)["settings"]["enabled"]
