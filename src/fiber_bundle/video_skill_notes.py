"""Place each audited V3 crop once, adjacent to its source segment and anchor."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import video_evidence_notes, video_skill_model
from .config import Config, InvalidInput, contained
from .inputs import canonical
from .query_policy_store import write_private
from .video_visual import DISCLOSURE, illustration_binding


def render(
    config: Config,
    policy: Any,
    frozen: dict[str, Any],
    job: Path,
    version: str,
    client: str,
    vault: str,
    save: Callable[[], None],
    guard: Callable[[], None],
) -> None:
    segments = [
        json.loads(line)
        for line in (job / "evidence/segments.jsonl").read_text().splitlines()
        if line
    ]
    placement = json.loads((job / "evidence/illustration_placement.json").read_bytes())["items"]
    source = json.loads((job / "metadata/source_metadata.json").read_bytes())
    body = f"# {source.get('title') or '视频笔记'}\n\n"
    visual_primary = frozen.get("transcript_route") == "visual_primary"
    if visual_primary:
        body += "> " + DISCLOSURE + "\n\n"
    if frozen.get("note_authoring") == video_evidence_notes.MODE:
        body += "> 生成方式：已有画面文字与分析要点直接排版，未调用 Pi 或新增模型推断。\n\n"
    bindings = []
    for segment in segments:
        guard()
        sid = segment["segment_id"]
        evidence = {
            "segment_id": sid,
            "start": segment["start"],
            "end": segment["end"],
            "transcript": segment.get("transcript", ""),
            "visual_evidence": [
                {"timestamp": f["timestamp"], "analysis": f.get("analysis")}
                for f in segment.get("frames", [])
            ],
        }
        if visual_primary:
            evidence.update(
                evidence_mode="visual_primary", speech_verified=False, limitation=DISCLOSURE
            )
        note = (
            video_evidence_notes.segment_note(evidence)
            if frozen.get("note_authoring") == video_evidence_notes.MODE
            else video_skill_model.author(
                config, policy, frozen, evidence, version, client, vault, save, guard
            )
        )
        if re.search(r"!\[|<img\b|!\[\[", note, flags=re.I):
            frozen.setdefault("rejected_note_runs", []).extend(
                rid for key, rid in frozen["note_runs"].items() if key.startswith(sid + ":")
            )
            save()
            raise InvalidInput("video_skill_note_image_invalid")
        body += f"\n## {sid} · {segment['start']:.1f}–{segment['end']:.1f}s\n\n{note}\n\n"
        for item in placement:
            if item["primary_segment_id"] != sid:
                continue
            path = item["path"]
            contained(job, path)
            binding = illustration_binding(item)
            anchor, caption = binding["note_anchor"], binding["caption"]
            alt = caption.replace("[", "\\[").replace("]", "\\]")
            body += f"{anchor}：{caption}\n\n"
            representative = item.get("representative_text") or item.get("segment_transcript") or ""
            if representative:
                body += "> 对应原文：" + representative.replace("\n", " ") + "\n\n"
            body += f"![{alt}](../{path})\n\n"
            bindings.append(binding)
    if len(bindings) != len(placement):
        raise InvalidInput("video_skill_illustration_segment_missing")
    coverage = json.loads((job / "evidence/coverage.json").read_bytes())
    body += "\n## 来源、覆盖与局限\n\n"
    body += f"- 来源：{source.get('webpage_url') or source.get('original_url') or frozen['url']}\n"
    body += f"- 转写路线：{frozen['transcript_route']}；默认术语库按应用策略采用。\n"
    body += (
        f"- 画面：{len(frozen.get('vision_runs', {}))} 个选择帧由已配置模型实际分析，"
        "未逐帧覆盖全视频。\n"
    )
    body += "- 弹幕、评论和直播聊天未提取、未使用。语义与专业结论未独立核验。\n"
    body += (
        "- 无适合裁剪的插图。\n"
        if not bindings
        else f"- 已引用 {len(bindings)} 张审计后的裁剪配图。\n"
    )
    body += "\n证据覆盖记录：\n```json\n" + canonical(coverage) + "\n```\n"
    write_private(
        job / "evidence/illustration_bindings.jsonl",
        ("\n".join(canonical(b) for b in bindings) + ("\n" if bindings else "")).encode(),
    )
    write_private(job / "note/note.md", body.encode())


def export(job: Path, frozen: dict[str, Any]) -> dict[str, bytes]:
    audit = json.loads((job / "metadata/audit_final.json").read_bytes())
    if audit.get("passed") is not True:
        raise InvalidInput("video_skill_final_audit_failed")
    body = (job / "note/note.md").read_text()
    files = {
        "note.md": body.replace("](../illustrations/", "](illustrations/").encode(),
        "video-audit.json": canonical(audit).encode(),
    }
    transcript = (
        (
            job
            / "transcript"
            / (
                "asr_normalized.srt"
                if frozen["transcript_route"] in {"local_qwen3_asr", "transcription_api"}
                else "subtitle_normalized.srt"
            )
        )
        if frozen["transcript_route"] != "visual_primary"
        else None
    )
    files["video-provenance.json"] = canonical(
        {
            "schema": "fiber-video-v3-provenance/v1",
            "job_id": frozen["job_id"],
            "source_media_sha256": frozen["source_media_sha256"],
            "skill_sha256": frozen["skill"]["sha256"],
            "terminology_sha256": frozen["skill"]["terminology_sha256"],
            "terminology_adoption": "application_policy",
            "transcript_route": frozen["transcript_route"],
            "transcript": transcript.read_text() if transcript else None,
            "visual_fallback": frozen.get("visual_fallback"),
            "asr_diagnostic": json.loads((job / "transcript/asr_diagnostic.json").read_bytes())
            if transcript is None
            else None,
            "asr": {
                "backend": frozen["skill"]["settings"].get("asr_backend", "local-qwen3"),
                "local_model": frozen["skill"]["settings"].get("asr_model", "Qwen/Qwen3-ASR-0.6B"),
                "api_model_configuration_id": frozen["skill"]["settings"].get(
                    "asr_api_model_configuration_id"
                ),
                "policy_sha256": frozen.get("asr_policy_sha256"),
                "timestamp_precision": "30-second-chunk" if frozen.get("asr_runs") else None,
                "runs": frozen.get("asr_runs", {}),
            },
            "vision_runs": frozen.get("vision_runs", {}),
            "note_authoring": frozen.get("note_authoring", "pi"),
            "note_runs": {}
            if frozen.get("note_authoring") == video_evidence_notes.MODE
            else frozen.get("note_runs", {}),
            "unused_prior_note_runs": frozen.get("note_runs", {})
            if frozen.get("note_authoring") == video_evidence_notes.MODE
            else {},
            "source_coverage": json.loads((job / "metadata/source_coverage.json").read_bytes()),
            "knowledge_verified": False,
        }
    ).encode()
    for item in json.loads((job / "illustrations/manifest.json").read_bytes())["items"]:
        if item["status"] == "created":
            files[item["path"]] = contained(job, item["path"]).read_bytes()
    return files
