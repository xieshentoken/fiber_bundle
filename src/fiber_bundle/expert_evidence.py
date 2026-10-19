"""Owner-authorized canonical package snapshots and derived, explicitly candidate notes."""

import io
import json
import sqlite3
import zipfile
from typing import Any

from . import expert_artifacts, expert_tasks
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .objects import digest
from .query_routing_files import read


def load(
    config: Config, db: sqlite3.Connection, client_id: str, identity: str
) -> tuple[sqlite3.Row, dict[str, Any], dict[str, bytes]]:
    source, _, file = expert_tasks.authorize(config, db, client_id, identity)
    task = expert_tasks.get(db, identity)
    if task is None or task["state"] != "completed":
        raise InvalidInput("expert_draft_unavailable")
    result = json.loads(task["result_json"])
    packet = result["artifact_manifest"]
    attempt = db.execute(
        "SELECT * FROM source_expert_attempts WHERE task_id=? AND ordinal=?",
        (identity, task["current_attempt"]),
    ).fetchone()
    output = contained(config.state_root, "source-expert/" + attempt["capture_id"]) / "output"
    captures = [p for p in (output / "result.json", output / "resume-result.json") if p.exists()]
    if len(captures) != 1 or digest(read(captures[0], 1024 * 1024)) != attempt["result_sha256"]:
        raise IntegrityError("expert_capture_changed")
    root = contained(output, packet["root"])
    files = expert_artifacts.tree(root)
    if (
        packet["files"] != expert_artifacts.manifest(files)
        or packet["source_sha256"] != file.sha256
    ):
        raise IntegrityError("expert_package_changed")
    return source, packet, files


def describe(
    config: Config, db: sqlite3.Connection, client_id: str, identity: str
) -> dict[str, Any]:
    source, packet, files = load(config, db, client_id, identity)
    version = db.execute(
        "SELECT source_version_id FROM receipts WHERE job_id=?", (source["receipt_id"],)
    ).fetchone()[0]
    index = json.loads(files["references/knowledge/index.json"])
    return {
        "manifest_sha256": digest(canonical(packet).encode()),
        "module_id": "expert:" + identity,
        "source_version_id": version,
        "source_sha256": packet["source_sha256"],
        "artifact_stage": "draft",
        "review_state": "deferred",
        "knowledge_verified": False,
        "objects": [json.loads(files[row["path"]]) for row in index["objects"]],
        "anchors": [
            json.loads(line) for line in files["references/evidence/anchors.jsonl"].splitlines()
        ],
        "catalog": json.loads(files["references/runtime/catalog.json"]),
        "files": sorted(files),
        "publication": publication(db, identity),
    }


def publication(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT state,relative_path,assessment_json FROM source_expert_publications "
        "WHERE task_id=?",
        (identity,),
    ).fetchone()
    from .publication_locations import wire

    if row is None:
        return None
    value = dict(row)
    assessment = json.loads(value.pop("assessment_json"))
    return {
        **value,
        **wire(db, "expert", identity),
        "completeness_status": assessment.get("completeness_status", "unknown"),
        "known_gaps": assessment.get("known_gaps", []),
    }


def bundle(config: Config, db: sqlite3.Connection, client_id: str, identity: str) -> bytes:
    _, _, files = load(config, db, client_id, identity)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return result.getvalue()


def render(
    source: dict[str, Any], packet: dict[str, Any], files: dict[str, bytes]
) -> dict[str, bytes]:
    index = json.loads(files["references/knowledge/index.json"])
    anchors = [json.loads(line) for line in files["references/evidence/anchors.jsonl"].splitlines()]
    by_anchor = {anchor["id"]: anchor for anchor in anchors}
    rendered_bytes = 0
    intended = {".fiber-expert-canonical/" + name: raw for name, raw in files.items()}
    catalog = [
        "# Full fake-expert Reference Draft",
        "",
        "Candidate synthesis; independent semantic Review deferred. knowledge_verified: false",
        "",
        "Source SHA256: " + source["source_sha256"],
        "",
    ]
    runtime_catalog = json.loads(files["references/runtime/catalog.json"])
    for figure in runtime_catalog.get("figures", []):
        catalog.extend(
            [
                "Figure ID: " + str(figure.get("id", "")),
                str(figure.get("label") or ""),
                str(figure.get("caption") or ""),
                "",
            ]
        )
    for number, row in enumerate(index["objects"], 1):
        record = json.loads(files[row["path"]])
        name = f"object-{number:04d}.md"
        # Canonical bytes stay intact; host notes remain candidate analyses.
        text = "\n".join(
            [
                "# " + record["title"],
                "",
                "source_kind: agent_analysis; artifact_stage: draft; "
                "review_state: deferred; knowledge_verified: false",
                "",
                "Canonical object ID: " + record["id"],
                "",
                "```json",
                canonical(record),
                "```",
                "",
                "## Evidence anchors",
                "```json",
                canonical([by_anchor[identity] for identity in record["evidence_ids"]]),
                "```",
                "",
            ]
        )
        raw = text.encode()
        rendered_bytes += len(raw)
        if rendered_bytes > expert_artifacts.MAX_TREE:
            raise InvalidInput("expert_host_note_capacity")
        intended[name] = raw
        catalog.append(f"- [{record['title']}]({name})")
    intended["note.md"] = ("\n".join(catalog) + "\n").encode()
    intended["provenance.json"] = canonical(
        {
            "schema": "fiber-expert-publication/v1",
            "source_version_id": source["version_id"],
            "asset_id": source["asset_id"],
            "source_sha256": source["source_sha256"],
            "artifact_manifest": packet,
            "artifact_stage": "draft",
            "review_state": "deferred",
            "knowledge_verified": False,
        }
    ).encode()
    return intended
