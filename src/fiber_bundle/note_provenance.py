"""Current note provenance shared by retrieval and explicitly selected graph snapshots."""

import sqlite3
from typing import Any

from . import note_index, note_inventory, publication_originals
from .config import Config


def describe(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    vault: sqlite3.Row,
    inventory: note_inventory.Inventory,
    note: note_index.Note,
) -> dict[str, Any]:
    pub = (
        inventory.publications.get(note.publication_id) if note.publication_id is not None else None
    )
    kind, label = "historical_note", "历史笔记，来源未核实"
    source_url = version = None
    document_spans = None
    derived_analysis = False
    video_note = False
    originals = []
    if pub is not None:
        original_hash = inventory.bindings[pub["id"]].original_sha256
        kind = "published_article" if note.sha256 == original_hash else "user_revision"
        label = "已发布原文" if kind == "published_article" else "用户修订，区别于原文"
        source_url, version = pub["source_url"], pub["version_id"]
        originals = publication_originals.visible(db, client_id, version, vault_hint=vault["id"])
    document = db.execute(
        "SELECT p.*,r.version_id FROM document_publications p "
        "JOIN execution_runs r ON r.id=p.run_id LEFT JOIN publication_locations l "
        "ON l.kind='document' AND l.publication_id=p.run_id WHERE COALESCE"
        "(l.vault_id,r.vault_id)=? "
        "AND p.state='published' AND COALESCE(l.entry_note_path,p.relative_path||'/note.md')=?",
        (vault["id"], note.relative_path),
    ).fetchone()
    if document is not None:
        import json

        from .publication_locations import get

        location = get(db, "document", document["run_id"])
        if location:
            document_spans = location["plan"].get("source_spans")
        original_hash = (
            location["plan"]["published_hashes"][note.relative_path]
            if location
            else json.loads(document["manifest_json"])["note.md"]["sha256"]
        )
        kind = "published_document" if note.sha256 == original_hash else "user_revision"
        label = (
            "人工审核文档，未独立核验" if kind == "published_document" else "用户修订，区别于提取稿"
        )
        version = document["version_id"]
        originals = publication_originals.visible(db, client_id, version, vault_hint=vault["id"])
    from . import document_parts

    part = document_parts.bindings(db, vault["id"], inventory.root, note.relative_path).get(
        note.relative_path
    )
    if part is not None:
        kind = (
            "published_document"
            if note.sha256 == (part.published_sha256 or part.original_sha256)
            else "user_revision"
        )
        label = (
            "人工审核文档，未独立核验" if kind == "published_document" else "用户修订，区别于提取稿"
        )
        version = part.version_id
        originals = publication_originals.visible(db, client_id, version, vault_hint=vault["id"])
        if part.role == "catalog":
            kind, label = "agent_analysis", "文档分片目录，导航管理信息，非原文证据"
            version, originals = None, []
    media = db.execute(
        "SELECT p.*,m.binding_json FROM media_publications p JOIN media_tasks m "
        "ON m.id=p.job_id LEFT JOIN publication_locations l "
        "ON l.kind='media' AND l.publication_id=p.job_id "
        "WHERE COALESCE(l.vault_id,m.vault_id)=? AND p.state='published' "
        "AND COALESCE(l.entry_note_path,p.relative_path||'/note.md')=?",
        (vault["id"], note.relative_path),
    ).fetchone()
    if media is not None:
        import json

        from .publication_locations import get

        location = get(db, "media", media["job_id"])
        original_hash = (
            location["plan"]["published_hashes"][note.relative_path]
            if location
            else json.loads(media["manifest_json"])["note.md"]["sha256"]
        )
        kind = "published_media" if note.sha256 == original_hash else "user_revision"
        label = (
            "人工审核媒体，未独立核验" if kind == "published_media" else "用户修订，区别于转录稿"
        )
        version = json.loads(media["binding_json"])["source"]["version_id"]
        originals = publication_originals.visible(db, client_id, version, vault_hint=vault["id"])
    from .publication_locations import get as layout_get

    intake = db.execute(
        "SELECT l.publication_id FROM publication_locations l WHERE l.vault_id=? "
        "AND l.kind='intake' AND (l.entry_note_path=? OR EXISTS(SELECT 1 "
        "FROM json_each(l.value,'$.published_hashes') WHERE key=?)) "
        "AND l.state='published'",
        (vault["id"], note.relative_path, note.relative_path),
    ).fetchone()
    if intake:
        location = layout_get(db, "intake", intake[0])
        assert location is not None
        source = location["plan"].get("intake_source")
        if source:
            version = source["source_version_id"]
            source_url = source.get("source_url")
            matches = note.sha256 == location["plan"]["published_hashes"][note.relative_path]
            kind = "published_channel_content" if matches else "user_revision"
            label = "人工审核通道资料，未独立核验" if matches else "用户修订，区别于提取稿"
            analysis_path = location["plan"]["mapping"].get("analysis.md")
            if source.get("analysis_sha256") == note.sha256 or (
                analysis_path == note.relative_path
                and location["plan"].get("supplement_roles", {}).get("analysis.md") == "analysis"
            ):
                derived_analysis = True
                kind = "agent_analysis" if matches else "user_revision"
                label = (
                    "Pi + Skill 分析草稿，非原始证据，未独立核验"
                    if matches
                    else "用户修订的分析草稿"
                )
            originals = publication_originals.visible(
                db, client_id, version, vault_hint=vault["id"]
            )
            if source.get("video_skill_sha256"):
                video_note = True
                derived_analysis = True
                kind = "agent_analysis" if matches else "user_revision"
    review_type = None
    review_actor = None
    quality_id = None
    review_run = (
        part.manifest["run_id"]
        if part is not None
        else (document["run_id"] if document is not None else None)
    )
    if review_run is not None:
        assessment = db.execute(
            "SELECT p.system_review_id,a.actor FROM document_publications p LE"
            "FT JOIN automatic_reviews a ON a.id=p.system_review_id WHERE p.ru"
            "n_id=?",
            (review_run,),
        ).fetchone()
        if assessment:
            review_type = "system" if assessment["system_review_id"] else "human"
            review_actor = assessment["actor"] or "local_admin"
            quality_id = assessment["system_review_id"]
            if kind == "published_document" and review_type == "system":
                label = "系统规则审核提取稿，未进行人工来源/视觉核对或独立核验"
    if pub is not None:
        assessment = db.execute(
            "SELECT a.actor,a.id FROM automatic_reviews a JOIN automatic_jobs j ON j.id=a.job_id "
            "JOIN article_publication_runs r ON r.run_id=j.source_id WHERE j.k"
            "ind='article' AND r.publication_id=? "
            "AND json_extract(a.value,'$.decision') IN ('accept','save_unchecked') "
            "ORDER BY a.created_at DESC LIMIT 1",
            (pub["id"],),
        ).fetchone()
        if assessment:
            review_type, review_actor = "system", assessment[0]
            quality_id = assessment[1]
    if media is not None and media["system_review_id"]:
        quality_id = media["system_review_id"]
        assessment = db.execute(
            "SELECT actor FROM automatic_reviews WHERE id=?", (media["system_review_id"],)
        ).fetchone()
        if assessment:
            review_type, review_actor = "system", assessment[0]
    if intake and location:
        source = location["plan"].get("intake_source", {})
        if source.get("review_type") in {"human", "system"}:
            review_type, review_actor = source["review_type"], source["review_actor"]
            if review_type == "system":
                quality_id = source.get("review_id")
    if review_type == "system" and kind not in {"user_revision", "agent_analysis"}:
        label = "系统结构审核提取稿，未进行人工来源/音画核对或独立核验"
    from .document_creation_save import search_source

    analysis = search_source(db, client_id, vault["id"], note.relative_path, note.sha256)
    if analysis is None:
        from .vault_merge_analysis import provenance as merged_analysis

        analysis = merged_analysis(db, client_id, vault["id"], note.relative_path, note.sha256)
    if analysis is not None:
        kind, label = analysis
        version, originals = None, []
    result = {
        "source_kind": kind,
        "source_label": label,
        "source_url": source_url,
        "source_version_id": version,
        "originals": originals,
        "review_type": review_type,
        "review_actor": review_actor,
        "completeness_status": "unknown",
    }
    if intake and location:
        quality_source = location["plan"].get("intake_source", {})
        if quality_source.get("processing_kind") == "video":
            result.update(
                completeness_status=quality_source.get("completeness_status", "unknown"),
                known_gaps=quality_source.get("known_gaps", []),
                save_basis=quality_source.get("save_basis"),
                source_kind=kind
                if kind in {"agent_analysis", "user_revision"}
                else "published_media",
                source_label="视频提取，未进行人工音画核对或独立核验",
            )
    if quality_id:
        import json

        quality = db.execute(
            "SELECT value FROM automatic_reviews WHERE id=?", (quality_id,)
        ).fetchone()
        if quality:
            value = json.loads(quality[0])
            result["completeness_status"] = value.get("completeness_status", "unknown")
            result["known_gaps"] = value.get("known_gaps", [])
            result["save_basis"] = value.get("save_basis", "checked_system_policy")
            if result["completeness_status"] == "not_checked" and kind != "agent_analysis":
                result["source_label"] = "未进行内容完整性校验的提取稿，按用户策略保存；未独立核验"
    if document_spans is not None and analysis is None:
        result["document_source_spans"] = {
            **document_spans,
            "matches_published": note.sha256 == document_spans["published_note_sha256"],
        }
    if part is not None and analysis is None:
        result["document_part"] = {
            "run_id": part.manifest["run_id"],
            "review_id": part.manifest["review_id"],
            "rendered_sha256": part.manifest["rendered_sha256"],
            "role": part.role,
            "rendered_start": part.entry.get("rendered_start"),
            "rendered_end": part.entry.get("rendered_end"),
            "source_units": part.entry.get("source_units", []),
            "source_units_scope": "reviewed_extraction",
            "original_part_sha256": part.original_sha256,
            "published_part_sha256": part.published_sha256 or part.original_sha256,
            "controlled_layout_projection": part.published_sha256 is not None,
            "matches_reviewed_extraction": (
                part.role == "extraction" and note.sha256 == part.original_sha256
            ),
        }
    if derived_analysis or analysis is not None or (part is not None and part.role == "catalog"):
        result["independent_evidence"] = False
    if result["source_kind"] == "agent_analysis":
        result["independent_evidence"] = False
        result["source_label"] = "模型分析草稿，非独立证据，未独立核验"
        if video_note:
            result["source_label"] = "视频提取笔记，包含模型整理，非独立证据，未独立核验"
    from .expert_publication import provenance

    expert = provenance(db, vault["id"], note.relative_path, note.sha256)
    if expert is not None:
        result.update(expert)
        result["originals"] = publication_originals.visible(
            db, client_id, expert["source_version_id"], vault_hint=vault["id"]
        )
    if result["source_kind"] == "user_revision":
        result.update(
            completeness_status="unknown", known_gaps=["user_revision_after_check"], save_basis=None
        )
    return result
