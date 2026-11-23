"""Real, isolated registry and MOC ledgers for the note-cloud v2 GUI fixture.

No cloud response is mocked. Low-level ledger seeding is limited to this new
synthetic state; real package validation and MOC plan/apply paths remain in use.
"""

import json
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from fiber_bundle import (
    database,
    moc_associations,
    moc_file_journal,
    moc_files,
    moc_store,
    note_identities,
)
from fiber_bundle.objects import digest
from fiber_bundle.organization import membership, packages, store


def tracked(config, client, vault, root: Path, relative: str) -> str:
    return note_identities.run(
        config,
        "Bearer " + client["token"],
        vault,
        "track",
        relative,
        digest((root / relative).read_bytes()),
    )["note_identity"]


def save_members(config, client, vault, mid, members) -> int:
    prior = moc_store.load(config, vault, mid)
    updated = replace(prior, revision=prior.revision + 1, members=members)
    moc_store.save(config, vault, mid, client["id"], updated, "synthetic-fixture", prior.revision)
    return updated.revision


def create_moc(config, client, vault, root: Path, title: str, members: dict, *, path=None) -> dict:
    mid = str(uuid4())
    relative = path or f"Map/{title}.md"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(f"---\ntype: moc\n---\n# {title}\n\n合成验收 MOC。\n")
    moc_associations.run(
        config,
        "Bearer " + client["token"],
        vault,
        "create",
        mid,
        title=title,
        revision=0,
    )
    save_members(config, client, vault, mid, members)
    return {"id": mid, "path": relative, "title": title}


def project(config, client, vault, root: Path, moc: dict, *, pending=False) -> dict:
    current = moc_store.load(config, vault, moc["id"])
    plan = moc_files.run(
        config,
        "Bearer " + client["token"],
        vault,
        "plan-file",
        moc["id"],
        revision=current.revision,
        relative=moc["path"],
        expected_sha256=digest((root / moc["path"]).read_bytes()),
    )
    if pending:
        packet = next(
            p
            for p in moc_file_journal.plans(config, vault, moc["id"])
            if p.plan_id == plan["plan_id"]
        )
        moc_file_journal.write(packet.path / "claim", b"claimed\n")
    else:
        moc_files.run(
            config,
            "Bearer " + client["token"],
            vault,
            "apply-file",
            moc["id"],
            plan_id=plan["plan_id"],
        )
    return plan


def directory_expert(config, client, vault, root: Path, ordinal: int) -> dict:
    relative = f"Book/fake-cloud-{ordinal:02d}"
    directory = root / relative
    catalog = {
        "schema_version": "tkc.runtime-catalog/v0.3",
        "objects": [],
        "figures": [],
        "package_id": f"fake-cloud-{ordinal:02d}",
        "module_id": f"cloud-{ordinal}",
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
    files = {
        "SKILL.md": f"# Synthetic Expert {ordinal}\n".encode(),
        "references/runtime/catalog.json": json.dumps(catalog).encode(),
    }
    for name, raw in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = {
        "schema_version": "tkc.expert-skill/v0.1",
        "build": {"compiler": "fake-expert"},
        "package": {
            "id": f"fake-cloud-{ordinal:02d}",
            "name": f"外置专家 / Expert {ordinal}",
            "version": "1",
            "status": "draft",
        },
        "capabilities": {"reference": True},
        "sources": [{"sha256": digest(f"synthetic-source-{ordinal}".encode())}],
        "integrity": {"files": {name: digest(raw) for name, raw in files.items()}},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with database.connect(config.state_root) as db:
        value = packages.inspect(config, db, client["id"], vault, relative)
        packages.commit(config, db, client["id"], vault, value, notify=False)
    return {
        "id": "expert:" + value["id"],
        "registry_id": value["id"],
        "path": relative,
        "version": value["version"],
    }


def markdown_expert(config, client, vault, root: Path) -> dict:
    """Seed an already-compiled native publication using the existing test helper.

    It creates real upload/provenance/publication rows and an ordinary entry MD;
    this is a metadata/dedup fixture, not a compiler or knowledge-quality test.
    """
    from test_expert_placement import publish_fixture
    from test_publication_layout import activate

    activate(config, vault)
    canonical = {
        "manifest.json": json.dumps(
            {
                "package": {
                    "id": "native-cloud-expert",
                    "name": "Markdown 专家 / Native Draft",
                    "status": "draft",
                }
            }
        ).encode(),
        "references/knowledge/index.json": b'{"objects":[]}',
        "references/evidence/anchors.jsonl": b"",
        "scripts/query_reference.py": b"raise RuntimeError('fixture runtime must never execute')\n",
        "references/runtime/catalog.json": (
            b'{"schema_version":"tkc.runtime-catalog/v0.3","objects":[],"figures":[]}'
        ),
    }
    files = {
        "note.md": "# Markdown 专家 / Native Draft\n\n合成专家入口，应该只显示一次。\n".encode()
    }
    files.update({".fiber-expert-canonical/" + name: raw for name, raw in canonical.items()})
    identity, location, _ = publish_fixture(config, client["id"], vault, files)
    # The older placement helper omits byte counts; provide the real immutable
    # file metadata expected by text-only native module admission.
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE source_expert_publications SET manifest_json=? WHERE task_id=?",
            (
                json.dumps(
                    {
                        "files": {
                            name: {"sha256": digest(raw), "byte_count": len(raw)}
                            for name, raw in files.items()
                        }
                    }
                ),
                identity,
            ),
        )
        prior = json.loads(
            db.execute("SELECT plan_json FROM source_imports WHERE id=?", (identity,)).fetchone()[0]
        )
        prior.setdefault("policy", {})["expert_runtime"] = {"fixture_disabled": True}
        db.execute(
            "UPDATE source_imports SET plan_json=? WHERE id=?", (json.dumps(prior), identity)
        )
    return {
        "id": "expert:" + identity,
        "registry_id": identity,
        "path": location["entry_note_path"],
        "version": "c" * 64,
    }


def history_note(config, client, vault, root: Path, relative: str, title: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\nSynthetic historical identity.\n")
    return tracked(config, client, vault, root, relative)


def small_cases(config, client, vault, root: Path) -> dict:
    expert = directory_expert(config, client, vault, root, 1)
    native = markdown_expert(config, client, vault, root)
    paths = [
        "材料/界面粘附.md",
        "材料/表面能.md",
        "材料/聚合物.md",
        "材料/接触角.md",
        "材料/复合材料.md",
    ]
    identities = {path: tracked(config, client, vault, root, path) for path in paths}
    indexed_path = "方法/不确定度.md"
    indexed_member = tracked(config, client, vault, root, indexed_path)
    historical_path = "Historic/已移走的研究记录.md"
    historical = history_note(config, client, vault, root, historical_path, "已移走的研究记录")
    restricted_path = "Restricted/PRIVATE-TITLE-SENTINEL.md"
    restricted = history_note(
        config, client, vault, root, restricted_path, "PRIVATE TITLE SENTINEL"
    )
    members = {
        identities[paths[0]]: ["manual"],
        identities[paths[1]]: ["manual"],
        identities[paths[2]]: ["system"],
        identities[paths[3]]: ["manual", "system"],
        expert["id"]: ["manual"],
        native["id"]: ["system"],
        historical: ["manual"],
        restricted: ["manual"],
    }
    main = create_moc(config, client, vault, root, "归属与人工锁定 MOC", members)
    main_file = root / main["path"]
    main_file.write_text(main_file.read_text() + "\n## 人工关联\n[[方法/不确定度]]\n")
    project(config, client, vault, root, main)
    store.put(
        config,
        "decisions",
        membership.decision_key(vault, identities[paths[0]], main["id"]),
        vault,
        {"entity": identities[paths[0]], "moc": main["id"], "state": "pinned"},
    )
    # Ledger ownership is committed; the current physical MOC still has the
    # previous revision's exact bytes and no link to this new member.
    members = {**members, identities[paths[4]]: ["manual"], indexed_member: ["manual"]}
    save_members(config, client, vault, main["id"], members)
    unknown = create_moc(
        config,
        client,
        vault,
        root,
        "文件证据待核对 MOC",
        {
            identities[paths[0]]: ["manual"],
            identities[paths[1]]: ["system"],
        },
    )
    project(config, client, vault, root, unknown)
    target = root / unknown["path"]
    data = target.read_bytes()
    from urllib.parse import quote

    encoded = quote("../" + paths[0], safe="/").encode()
    target.write_bytes(
        b"\n".join(line for line in data.split(b"\n") if encoded not in line)
        + "\n人工变更，仅合成验收。\n".encode()
    )
    pending = create_moc(
        config, client, vault, root, "待写入计划 MOC", {identities[paths[2]]: ["manual"]}
    )
    project(config, client, vault, root, pending)
    save_members(
        config,
        client,
        vault,
        pending["id"],
        {identities[paths[2]]: ["manual"], identities[paths[3]]: ["manual"]},
    )
    project(config, client, vault, root, pending, pending=True)
    # Delete only synthetic files after all write APIs have finished. A scope
    # denial then ensures historical membership cannot reveal an old title/path.
    (root / historical_path).unlink()
    (root / restricted_path).unlink()
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE vaults SET scope_json=? WHERE id=?",
            (json.dumps({"include": [""], "exclude": ["Restricted"]}), vault),
        )
    return {
        "experts": [expert, native],
        "mocs": {"ownership": main, "unknown": unknown, "pending": pending},
        "cases": {
            "manual_pinned": identities[paths[0]],
            "manual_unpinned": identities[paths[1]],
            "system": identities[paths[2]],
            "mixed_owners": identities[paths[3]],
            "saved_not_synced": identities[paths[4]],
            "indexed_present": indexed_member,
            "history_missing": historical,
            "restricted_missing": restricted,
        },
        "private_sentinel": "PRIVATE-TITLE-SENTINEL",
    }


def dense_cases(config, client, vault, root: Path) -> dict:
    experts = [directory_expert(config, client, vault, root, ordinal + 20) for ordinal in range(7)]
    deleted = []
    identities = []
    for index in range(8):
        relative = f"Historic/缺失目标-{index}.md"
        identities.append(
            history_note(
                config, client, vault, root, relative, f"缺失目标 / Missing target {index}"
            )
        )
        deleted.append(root / relative)
    moc = create_moc(
        config,
        client,
        vault,
        root,
        "材料界面 MOC",
        {
            **{entry["id"]: ["manual"] for entry in experts},
            **{identity: ["system"] for identity in identities},
        },
        path="Map/材料界面 MOC.md",
    )
    project(config, client, vault, root, moc)
    for path in deleted:
        path.unlink()
    return {"experts": experts, "moc": moc, "missing_identities": identities}


def confirmed_missing_intake(config, client, vault: str, root: Path) -> dict:
    """Append one synthetic completed-marker case without observing/recovering anything.

    The synthetic note is registered and then removed. The production-shaped
    location and completed availability marker are seeded directly;
    publication_availability.observe/apply and all real data are outside scope.
    """
    import base64

    client_id = client["id"] if isinstance(client, dict) else client
    from fiber_bundle import (
        note_identity_store,
        note_index,
        publication_locations,
        vault_save_policy,
    )
    from fiber_bundle.inputs import canonical

    publication = str(uuid4())
    operation = str(uuid4())
    title = "已确认缺失的发布笔记 · Confirmed missing intake"
    raw = f"# {title}\n\nOnly a synthetic publication fixture.\n".encode()
    relative = "Historic/confirmed-missing-intake.md"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError("Confirmed-missing fixture was already added")
    target.write_bytes(raw)
    note = note_index.read_note(root, relative, note_index.Limits().note_bytes)
    assert note.file_key is not None
    root_key = [root.stat().st_dev, root.stat().st_ino]
    with database.connect(config.state_root) as db:
        source = db.execute(
            "SELECT r.source_version_id FROM receipts r JOIN source_versions v "
            "ON v.id=r.source_version_id JOIN sources s ON s.id=v.source_id "
            "WHERE s.vault_id=? AND r.state='stored' ORDER BY r.rowid LIMIT 1",
            (vault,),
        ).fetchone()[0]
        plan = {
            "layout_version": 2,
            "mapping": {"note.md": relative},
            "body_files": ["note.md"],
            "outputs": {relative: base64.b64encode(raw).decode()},
            "logical_hashes": {"note.md": note.sha256},
            "published_hashes": {relative: note.sha256},
            "file_keys": {relative: list(note.file_key)},
            "root_key": root_key,
            "policy_binding": {"settings": vault_save_policy.get(config, db, vault)["settings"]},
            "identity_overrides": {},
            "intake_source": {"source_version_id": source},
        }
        identity = publication_locations.note_id("intake", publication, "note.md")
        registered = note_identity_store.Registration(
            identity,
            None,
            relative,
            note.sha256,
            note.file_key,
            (root.stat().st_dev, root.stat().st_ino),
            1,
        )
        note_identity_store.save(config, vault, client_id, registered)
        store.put(
            config,
            "entities",
            identity,
            vault,
            {
                "id": identity,
                "path": relative,
                "title": title,
                "sha256": note.sha256,
                "file_key": list(note.file_key),
                "_inventory_identity": identity,
            },
        )
        payload = {
            "client_id": client_id,
            "vault_id": vault,
            "kind": "intake",
            "publication_id": publication,
            "revision": 1,
            "entry_note_path": relative,
            "missing_paths": [relative],
            "root_key": root_key,
            "location_sha256": digest(canonical(plan).encode()),
            "expires_at": time.time() + 600,
        }
        target.unlink()
        plan["availability"] = {
            "state": "missing",
            "operation_id": operation,
            "missing_paths": [relative],
        }
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,'completed',?,?,?)",
            (operation, vault, "publication_missing", canonical(payload), time.time(), time.time()),
        )
        db.execute(
            "INSERT INTO publication_locations VALUES('intake',?,?,?,'',2,'reconcile',?)",
            (publication, vault, relative, canonical(plan)),
        )
    return {
        "identity": identity,
        "publication_id": publication,
        "operation_id": operation,
        "path": relative,
        "title": title,
        "label": title,
        "source_version_id": source,
        "reason": "publication_missing_confirmed",
        "synthetic_append": True,
    }


def summarize(value: dict) -> dict:
    linked = {endpoint for edge in value["edges"] for endpoint in (edge["source"], edge["target"])}
    kinds = {
        kind: sum(node["kind"] == kind for node in value["nodes"])
        for kind in ("note", "moc", "expert", "unresolved")
    }
    return {
        "schema": value["schema"],
        "nodes": len(value["nodes"]),
        "edges": len(value["edges"]),
        "node_kinds": kinds,
        "mocs": kinds["moc"],
        "isolates": sum(node["id"] not in linked for node in value["nodes"]),
        "coverage": value["coverage"],
    }


def validate_complete(value: dict, fixture: dict, name: str) -> dict:
    """Fail fixture preparation if any intended v2 scenario is missing."""
    assert value["schema"] == "fiber-note-cloud/v2"
    nodes = {node["id"]: node for node in value["nodes"]}
    confirmed = fixture.get("confirmed_missing_intake")
    expected_experts, expected_missing = (2, 2 + bool(confirmed)) if name == "personal" else (7, 8)
    assert sum(node["kind"] == "expert" for node in nodes.values()) == expected_experts
    assert sum(node["kind"] == "unresolved" for node in nodes.values()) == expected_missing
    assert all(edge["source"] in nodes and edge["target"] in nodes for edge in value["edges"])
    if name != "personal":
        assert len(nodes) == 2515
        return {"expert_count": expected_experts, "unresolved_count": expected_missing}

    payload = json.dumps(value, ensure_ascii=False)
    assert "PRIVATE-TITLE-SENTINEL" not in payload and "PRIVATE TITLE SENTINEL" not in payload
    native = fixture["experts"][1]
    assert sum(node.get("path") == native["path"] for node in nodes.values()) == 1
    assert nodes[native["id"]]["kind"] == "expert"
    anonymous = [
        node
        for node in nodes.values()
        if node["kind"] == "unresolved" and not node["metadata_visible"]
    ]
    assert len(anonymous) == 1
    assert anonymous[0]["path"] == "" and anonymous[0]["entity_id"] is None
    assert anonymous[0]["aliases"] == [] and anonymous[0]["reason"] == "target_unavailable"
    visible_missing = next(
        node
        for node in nodes.values()
        if node.get("entity_id") == fixture["cases"]["history_missing"]
    )
    assert visible_missing["kind"] == "unresolved"
    assert visible_missing["reason"] == "identity_not_indexed"

    def member(case: str, target_moc: str = "ownership") -> dict:
        moc_path = fixture["mocs"][target_moc]["path"]
        mid = next(node["id"] for node in nodes.values() if node["path"] == moc_path)
        entity = fixture["cases"][case]
        target = next(node["id"] for node in nodes.values() if node.get("entity_id") == entity)
        return next(
            edge["membership"]
            for edge in value["edges"]
            if edge["kind"] == "moc" and {edge["source"], edge["target"]} == {mid, target}
        )

    expected = {
        "manual_pinned": (["manual"], True),
        "manual_unpinned": (["manual"], False),
        "system": (["system"], False),
        "mixed_owners": (["manual", "system"], False),
    }
    receipts = {}
    for case, (owners, locked) in expected.items():
        item = member(case)
        assert item["owners"] == owners and item["manual_locked"] is locked
        receipts[case] = item
    receipts["saved_not_synced"] = member("saved_not_synced")
    assert receipts["saved_not_synced"]["file_sync"] == "not_synced"
    receipts["indexed_present"] = member("indexed_present")
    assert receipts["indexed_present"]["file_sync"] == "indexed_present"
    receipts["changed_moc_file"] = member("manual_pinned", "unknown")
    assert receipts["changed_moc_file"]["file_sync"] == "unknown"
    assert receipts["changed_moc_file"]["evidence"] == "indexed_file_differs_from_applied"
    receipts["pending_moc_plan"] = member("system", "pending")
    assert receipts["pending_moc_plan"]["file_sync"] == "unknown"
    assert receipts["pending_moc_plan"]["evidence"] == "pending_moc_file_plan"
    if confirmed:
        target = next(
            node for node in nodes.values() if node.get("entity_id") == confirmed["identity"]
        )
        assert target["kind"] == "unresolved" and target["availability"] == "missing"
        assert target["reason"] == "publication_missing_confirmed"
        assert target["label"] == confirmed["title"] and target["path"] == confirmed["path"]
        receipts["confirmed_missing_intake"] = member("confirmed_publication")
        assert receipts["confirmed_missing_intake"]["owners"] == ["manual"]
    return {
        "node_count": len(nodes),
        "expert_deduplicated": True,
        "private_metadata_hidden": True,
        "memberships": receipts,
    }
