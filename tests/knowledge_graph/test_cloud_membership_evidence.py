"""Saved ownership receipts and synchronization proof stay separate."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from note_cloud_complete_fixture import save_members

from fiber_bundle import database, moc_markdown, moc_store, note_index
from fiber_bundle.knowledge_graph import (
    cloud,
    cloud_entities,
    cloud_ledger,
    cloud_memberships,
    cloud_sources,
)
from fiber_bundle.organization import membership, store

from .test_cloud_complete import complete_library as complete_library
from .test_cloud_complete import read
from .test_cloud_web_contract import validate_frontend


def test_markdown_expert_aliases_preserve_parallel_saved_ownership(config, complete_library):
    client, vault, _, fixture = complete_library
    before = read(config, complete_library)
    native = fixture["experts"][1]
    node = next(node for node in before["nodes"] if node["id"] == native["id"])
    alias = next(alias for alias in node["aliases"] if alias.startswith(("note:", "publication:")))
    mid = fixture["mocs"]["ownership"]["id"]
    record = moc_store.load(config, vault, mid)
    save_members(config, client, vault, mid, {**record.members, alias: ["manual"]})
    store.put(
        config,
        "decisions",
        membership.decision_key(vault, alias, mid),
        vault,
        {"entity": alias, "moc": mid, "state": "pinned"},
    )
    value = read(config, complete_library)
    moc = next(
        node["id"]
        for node in value["nodes"]
        if node["path"] == fixture["mocs"]["ownership"]["path"]
    )
    receipts = [
        edge
        for edge in value["edges"]
        if edge["kind"] == "moc" and edge["source"] == moc and edge["target"] == native["id"]
    ]
    assert len(receipts) == len({edge["id"] for edge in receipts}) == 2
    assert {
        (tuple(e["membership"]["owners"]), e["membership"]["manual_locked"]) for e in receipts
    } == {
        (("manual",), True),
        (("system",), False),
    }
    assert sum(node["id"] == native["id"] for node in value["nodes"]) == 1
    assert value["coverage"]["membership_edges"] == before["coverage"]["membership_edges"] + 1
    with database.connect(config.state_root) as db:
        found = cloud.search(config, db, client["id"], vault, "Markdown")
    assert value["snapshot_token"] == found["snapshot_token"]
    validate_frontend(value, found, {"client_id": client["id"], "vault_id": vault})


@pytest.mark.parametrize(
    "surround",
    [
        lambda line: "```md\n" + line + "\n```",
        lambda line: "~~~\n" + line + "\n~~~",
        lambda line: "<!--\n" + line + "\n-->",
        lambda line: "    " + line,
        lambda line: "\t" + line,
        lambda line: "示例：" + line,
    ],
    ids=["backtick-fence", "tilde-fence", "comment", "four-spaces", "tab", "prose"],
)
def test_only_trusted_managed_identity_lines_establish_sync(surround):
    mid, real, decoy = str(uuid4()), "expert:" + str(uuid4()), "expert:" + str(uuid4())
    begin, end = (part.decode() for part in moc_markdown.markers(mid))

    def line(identity):
        return f"- 专家 Skill：`{identity}`（通过 Fiber Agent 接口调用）"

    text = "\n".join(["# MOC", line(decoy), begin, line(real), surround(line(decoy)), end, ""])
    note = note_index.Note("Map/topic.md", "a" * 64, "MOC", "")
    evidence = cloud_memberships.observe(text, mid, note, {})
    assert evidence.identities == frozenset({real})


@pytest.mark.parametrize(
    "prefix,suffix,visible",
    [
        ("", "", True),
        ("```md\n", "\n```", False),
        ("~~~\n", "\n~~~", False),
        ("<!--\n", "\n-->", False),
    ],
    ids=["normal-block", "outer-backtick-fence", "outer-tilde-fence", "outer-comment"],
)
def test_outer_context_cannot_turn_examples_into_managed_sync_evidence(prefix, suffix, visible):
    mid, identity = str(uuid4()), "expert:" + str(uuid4())
    begin, end = (part.decode() for part in moc_markdown.markers(mid))
    block = "\n".join(
        [
            begin,
            f"- 专家 Skill：`{identity}`（通过 Fiber Agent 接口调用）",
            "- [Target](../Target.md)",
            end,
        ]
    )
    text = prefix + block + suffix
    note = note_index.Note("Map/topic.md", "a" * 64, "MOC", "")
    evidence = cloud_memberships.observe(text, mid, note, {"Target.md": "current"})
    assert (identity in evidence.identities) is visible
    assert ("current" in evidence.targets) is visible


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[[Target]]", True),
        ("`[[Target]]`", False),
        ("<!-- [[Target]] -->", False),
        ("    [[Target]]", False),
        ("\t[[Target]]", False),
        ("```md\n[[Target]]\n```", False),
    ],
    ids=["real-link", "inline-code", "comment", "four-spaces", "tab", "fence"],
)
def test_sync_link_evidence_preserves_original_code_and_comment_semantics(text, expected):
    note = note_index.Note("Map/topic.md", "a" * 64, "MOC", "")
    evidence = cloud_memberships.observe(text, str(uuid4()), note, {"Target.md": "current"})
    assert ("current" in evidence.targets) is expected


def test_applied_binding_from_another_vault_root_is_not_current_evidence(config, complete_library):
    client, vault, *_ = complete_library
    ledger = cloud_ledger.read(config, vault)
    mid = complete_library[3]["mocs"]["ownership"]["id"]
    plans = deepcopy(ledger.plans)
    plans[mid]["vault_key"][1] += 1
    other_root = replace(ledger, plans=plans)
    with patch.object(cloud_ledger, "read", return_value=other_root):
        value = read(config, complete_library)
    moc = next(
        node["id"]
        for node in value["nodes"]
        if node["path"] == complete_library[3]["mocs"]["ownership"]["path"]
    )
    assert not any(edge["kind"] == "moc" and edge["source"] == moc for edge in value["edges"])
    assert value["coverage"]["moc_membership_unavailable"] >= len(ledger.records[mid].members)
    assert not value["coverage"]["membership_projection_complete"]
    assert not value["coverage"]["complete"]


def test_absent_reconstruction_proof_cannot_upgrade_saved_memberships(config, complete_library):
    client, vault, _, fixture = complete_library
    with database.connect(config.state_root) as db:
        source = cloud_sources.read(config, db, client["id"], vault)
        table = cloud_entities.build(config, db, client["id"], vault, source)
    edges, _ = cloud_memberships.edges(source, table, {}, vault=vault)
    path = fixture["mocs"]["ownership"]["path"]
    moc = table.paths[path]
    receipts = [edge["membership"] for edge in edges if edge["source"] == moc]
    assert receipts
    assert all(item["file_sync"] == "unknown" for item in receipts)
    assert all(item["evidence"] == "indexed_moc_unavailable" for item in receipts)
    assert any(item["manual_locked"] for item in receipts)


def test_changed_applied_file_and_pending_claim_remain_unknown(config, complete_library):
    value = read(config, complete_library)
    fixture = complete_library[3]
    by_path = {node["path"]: node["id"] for node in value["nodes"]}
    for category, reason in [
        ("unknown", "indexed_file_differs_from_applied"),
        ("pending", "pending_moc_file_plan"),
    ]:
        moc = by_path[fixture["mocs"][category]["path"]]
        receipts = [
            edge["membership"]
            for edge in value["edges"]
            if edge["kind"] == "moc" and edge["source"] == moc
        ]
        assert receipts
        assert all(
            item["file_sync"] == "unknown" and item["evidence"] == reason for item in receipts
        )
