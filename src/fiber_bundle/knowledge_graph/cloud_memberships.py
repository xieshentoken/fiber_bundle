"""Saved ownership and conservative, cache-only MOC synchronization evidence."""

import re
from dataclasses import dataclass
from typing import Any

from .. import moc_markdown, note_index
from ..config import IntegrityError
from ..note_transfer_links import mask
from . import cloud_sources, projection
from .cloud_entities import Table
from .contracts import fingerprint


@dataclass(frozen=True)
class Evidence:
    identities: frozenset[str]
    targets: frozenset[str]


def observe(text: str, mid: str, note: note_index.Note, paths: dict[str, str]) -> Evidence:
    from ..source_import_markdown import references

    targets = {value for ref in references(text) if (value := projection.resolve(note, ref, paths))}
    identities = set()
    try:
        prefix, block, _ = moc_markdown.split(text.encode(), mid)
    except IntegrityError:
        return Evidence(frozenset(), frozenset(targets))
    # A marker inside an already open comment does not establish a managed
    # region. Checking the prefix also avoids treating its own nested marker
    # comment terminator as the end of an outer comment.
    if "<!--" in mask(prefix.decode()):
        return Evidence(frozenset(), frozenset())
    # Mask the entire note before narrowing to its verified block, retaining
    # fence/comment context from outside the block. Inline code is intentionally
    # kept in the raw canonical line: expert IDs are emitted in backticks by the
    # writer, while the visible prefix/suffix prove the line itself is not code.
    visible_lines = mask(text).splitlines()
    offset = prefix.decode().count("\n")
    for number, line in enumerate(block.decode().splitlines(), offset):
        if number >= len(visible_lines) or not visible_lines[number].strip():
            continue
        match = re.fullmatch(
            r"- 专家 Skill：`(expert:[a-f0-9-]{36})`（通过 Fiber Agent 接口调用）", line
        )
        if match is None:
            match = re.fullmatch(
                r"- 当前不可见：((?:note|publication):[a-f0-9-]{36})（归属保留）", line
            )
        if match:
            identities.add(match[1])
    return Evidence(frozenset(identities), frozenset(targets))


def edges(
    source: cloud_sources.Snapshot, table: Table, observed: dict[str, Evidence], *, vault: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = []
    counts = {
        "membership_edges": 0,
        "manual_memberships": 0,
        "system_memberships": 0,
        "manual_locked_memberships": 0,
        "sync_indexed_present": 0,
        "sync_not_synced": 0,
        "sync_unknown": 0,
    }
    ledger = table.ledger
    if ledger is None:
        return [], {**counts, "moc_membership_unavailable": 0}
    omitted = table.omitted_memberships
    for mid, path in table.moc_paths.items():
        record = ledger.records[mid]
        plan = ledger.plans.get(mid)
        note = source.notes[path]
        moc = table.paths[path]
        for identity, owners in record.members.items():
            target = table.aliases.get(identity)
            if target is None or target == moc:
                continue
            decision = ledger.decisions.get(vault + ":" + identity + ":" + mid)
            locked = bool(
                decision
                and decision.get("entity") == identity
                and decision.get("moc") == mid
                and decision.get("state") == "pinned"
            )
            state, evidence = "unknown", "indexed_evidence_absent"
            if mid in ledger.pending:
                evidence = "pending_moc_file_plan"
            elif source.coverage.get("index_state") != "ready" or source.coverage.get("pending", 0):
                evidence = "index_not_current"
            elif path not in observed:
                evidence = "indexed_moc_unavailable"
            elif plan is None or note.sha256 != plan["after_sha256"]:
                evidence = "indexed_file_differs_from_applied"
            elif identity in observed[path].identities:
                state, evidence = "indexed_present", "indexed_managed_identity"
            elif target in observed[path].targets:
                state, evidence = "indexed_present", "indexed_note_link"
            elif record.revision > plan["revision"]:
                state, evidence = "not_synced", "ledger_newer_than_indexed_applied_file"
            membership = {
                "owners": owners.copy(),
                "manual_locked": locked,
                "file_sync": state,
                "evidence": evidence,
            }
            result.append(
                {
                    # Distinct saved member identities can resolve to the same
                    # current expert. Keep both ownership receipts as parallel
                    # edges; their hashes reveal no anonymous source identity.
                    "id": fingerprint([moc, target, identity, "moc"]),
                    "source": moc,
                    "target": target,
                    "kind": "moc",
                    "membership": membership,
                }
            )
            counts["membership_edges"] += 1
            counts["manual_memberships"] += "manual" in owners
            counts["system_memberships"] += "system" in owners
            counts["manual_locked_memberships"] += locked
            counts["sync_" + state] += 1
    return result, {
        **counts,
        "moc_membership_unavailable": omitted,
        "moc_membership_source": "saved_ledger",
        "membership_projection_complete": omitted == 0,
    }
