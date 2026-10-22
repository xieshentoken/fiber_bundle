import hashlib

import pytest

from fiber_bundle import auth, database
from fiber_bundle.config import InvalidInput
from fiber_bundle.suppliers import legacy, normalize, service
from fiber_bundle.suppliers import store as supplier_store


def note(path, text):
    return {
        "path": path,
        "title": path.split("/")[-1].removesuffix(".md"),
        "text": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "observation": [1, 2, 3, 4, 5],
    }


def samples():
    company = """---
type: supplier
supplier_id: SUP-A
---
# Synthetic vendor
<!-- GF_AGENT_MANAGED_START -->
- **model**：Alpha（provisional；SRC-20260101-AAAAAAAAAA）
- **model**：Beta（provisional；SRC-20260101-BBBBBBBBBB）
<!-- GF_AGENT_MANAGED_END -->
## 用户补充与修正区
User correction remains intact.
## 待确认事项
"""
    first = """---
type: supplier_source
source_id: SRC-20260101-AAAAAAAAAA
created: 2026-01-01
status: complete
confidentiality: no_external_ai
---
<!-- GF_AGENT_MANAGED_START -->
- **item_name**：金刚石粉（`SRC-20260101-AAAAAAAAAA-E0001`）
- **model**：Alpha（`SRC-20260101-AAAAAAAAAA-E0002`）
- **price**：590 CNY 元/100g（`SRC-20260101-AAAAAAAAAA-E0003`）
<!-- GF_AGENT_MANAGED_END -->"""
    second = """---
type: supplier_source
source_id: SRC-20260101-BBBBBBBBBB
status: complete
---
<!-- GF_AGENT_MANAGED_START -->
- **model**：Beta（`SRC-20260101-BBBBBBBBBB-E0001`）
- **particle_size**：60/70（`SRC-20260101-BBBBBBBBBB-E0002`）
<!-- GF_AGENT_MANAGED_END -->"""
    return [
        note("01-供应商/SUP-A Synthetic.md", company),
        note("03-来源记录/source-a.md", first),
        note("03-来源记录/source-b.md", second),
    ]


def test_products_do_not_borrow_other_sources_specs():
    result = legacy.project(samples())
    quotes = result["records"]["quote"]
    products = {p["id"]: p for p in result["records"]["product"]}
    assert len(quotes) == 1
    product = products[quotes[0]["product_id"]]
    assert product["model"] == "Alpha" and "particle_size" not in product["specs"]
    assert quotes[0]["quoted_at"] is None and quotes[0]["tax"] == "unknown"
    assert quotes[0]["amount"] == "590" and quotes[0]["unit"] == "100g"
    assert all(
        s["original_state"] == "missing" and not s["knowledge_verified"]
        for s in result["records"]["source"]
    )
    assert result["records"]["source"][0]["legacy_confidentiality"] == "no_external_ai"
    assert "User correction remains intact." in result["records"]["supplier"][0]["user_corrections"]
    assert normalize.convert("590", "100g", "kg") == 5900
    assert normalize.convert("5", "piece", "kg") is None


def test_empty_facts_and_placeholders_are_not_successful_facts():
    notes = samples()
    notes[1] = note("03-来源记录/source-a.md", notes[1]["text"].replace("金刚石粉", "/材料编号："))
    result = legacy.project(notes)
    assert any(i["reason"] == "product_placeholder" for i in result["records"]["issue"])
    assert not any(p["name"] == "/材料编号：" for p in result["records"]["product"])
    empty = note(
        "03-来源记录/empty.md",
        "---\ntype: supplier_source\nsource_id: SRC-20260101-CCCCCCCCCC\n"
        "status: complete\n---\n未提取到可安全写入的结构化事实。",
    )
    result = legacy.project([empty])
    assert result["counts"]["issue"] == 2
    assert result["records"]["source"][0]["extraction_state"] == "historical_record"


def fixture(config, store):
    root = config.project_root / "test vault/temp_vault2"
    root.mkdir(parents=True, exist_ok=True)
    for n in samples():
        p = root / n["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(n["text"])
    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "synthetic supplier", False)
        vault = db.execute("select id from vaults where name='supplier'").fetchone()[0]
        auth.set_grant(db, client["id"], vault, query=True, original=True, add=True)
    return root, client["id"], vault


def test_isolated_import_is_idempotent_and_read_only(config, store):
    root, client, vault = fixture(config, store)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.md")}
    with database.locked(config), database.connect(config.state_root) as db:
        preview = service.preview(config, db, client, vault)
        result = service.import_legacy(
            config, db, client, vault, "request", preview["snapshot_sha256"]
        )
        assert (
            service.import_legacy(config, db, client, vault, "request", preview["snapshot_sha256"])
            == result
        )
        service.validate_sources(config, db, client, vault)
        assert len(supplier_store.rows(config, vault, "supplier")) == 1
        p = root / "03-来源记录/source-a.md"
        p.write_text(p.read_text() + "\nChanged")
        with pytest.raises(InvalidInput, match="supplier_legacy_changed"):
            service.import_legacy(config, db, client, vault, "later", preview["snapshot_sha256"])
        with pytest.raises(InvalidInput, match="supplier_sources_changed"):
            service.validate_sources(config, db, client, vault)
    assert all(
        p.read_bytes() == v for n, v in before.items() if (p := root / n).name != "source-a.md"
    )


def test_legacy_import_rechecks_query_and_add(config, store):
    root, client, vault = fixture(config, store)
    with database.locked(config), database.connect(config.state_root) as db:
        preview = service.preview(config, db, client, vault)
        auth.set_grant(db, client, vault, query=True, original=True, add=False)
        with pytest.raises(auth.Denied):
            service.import_legacy(config, db, client, vault, "x", preview["snapshot_sha256"])
    assert not (config.state_root / "supplier-intelligence.sqlite3").exists()


def test_reimport_keeps_snapshots_and_retires_removed_quotes(config, store):
    root, client, vault = fixture(config, store)
    with database.locked(config), database.connect(config.state_root) as db:
        one = service.preview(config, db, client, vault)
        service.import_legacy(config, db, client, vault, "one", one["snapshot_sha256"])
        p = root / "03-来源记录/source-a.md"
        p.write_text(
            p.read_text().replace(
                "- **price**：590 CNY 元/100g（`SRC-20260101-AAAAAAAAAA-E0003`）", ""
            )
        )
        two = service.preview(config, db, client, vault)
        service.import_legacy(config, db, client, vault, "two", two["snapshot_sha256"])
        service.validate_sources(config, db, client, vault)
    assert len(supplier_store.rows(config, vault, "snapshot")) == 2
    assert supplier_store.rows(config, vault, "quote")[0]["active"] is False
    assert supplier_store.rows(config, vault, "history")


def test_alias_is_explicit_and_multi_model_price_is_unresolved():
    notes = samples()
    notes.append(
        note(
            "01-供应商/SUP-BEE Alias.md",
            '---\ntype: supplier_alias\ncanonical_supplier: "[[SUP-A Synthetic]]"\n---\n',
        )
    )
    notes[1] = note(
        notes[1]["path"],
        notes[1]["text"].replace(
            "<!-- GF_AGENT_MANAGED_END -->",
            "- **model**：Gamma（`SRC-20260101-AAAAAAAAAA-E0004`）\n<!-- GF_AGENT_MANAGED_END -->",
        ),
    )
    result = legacy.project(notes)
    assert result["records"]["alias"][0]["canonical_id"] == "SUP-A"
    assert result["records"]["quote"][0]["product_id"] is None
    assert any(i["reason"] == "quote_product_unresolved" for i in result["records"]["issue"])
