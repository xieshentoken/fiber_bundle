"""Finite query diagnostics and one durable, local-only pre-answer evidence retry."""

from typing import Any

from ..config import IntegrityError, InvalidInput

RETRY_KEY = "local_evidence_retry"
RETRY_SCHEMA = "channel-local-evidence-retry/v1"
CLOUD_RETRY = {
    "cloud_metadata_unavailable": "channel_cloud_metadata_unavailable",
    "cloud_status_check_timeout": "channel_cloud_check_timeout",
    "cloud_item_pending_stale_or_conflicted": "channel_cloud_not_ready",
}
INTEGRITY_CODES = {
    **CLOUD_RETRY,
    "cloud_item_not_downloaded": "channel_cloud_not_downloaded",
    "cloud_symlink_not_allowed": "channel_source_verification_failed",
    "cloud_status_helper_changed": "channel_source_verification_failed",
    "channel_source_changed": "channel_source_changed",
    **{
        reason: "channel_source_changed"
        for reason in (
            "search index differs from committed layout",
            "search evidence changed before delivery",
            "search title changed before delivery",
            "search index passage differs from current evidence",
            "search note identity changed before delivery",
            "search index identity differs from current registration",
            "search publication path differs from current evidence",
            "search publication identity changed before delivery",
            "note index source changed",
        )
    },
}
MESSAGES = {
    "channel_cloud_metadata_unavailable": "iCloud 暂未提供可验证的文件状态，尚未读取未确认的资料。",
    "channel_cloud_check_timeout": "iCloud 文件状态检查超时，尚未交付未经核对的资料。",
    "channel_cloud_not_ready": "iCloud 资料尚未通过同步状态或文件一致性检查。",
    "channel_cloud_not_downloaded": "所需 iCloud 文件尚未下载到本机。",
    "channel_source_verification_failed": "来源安全校验未通过。",
    "channel_source_changed": "资料或索引与已核对的来源不一致，已停止交付。",
}
ANSWER_FORMAT_MESSAGES = {
    "answer_draft_rejected_citation_in_text": "模型回复的引用格式未通过校验。",
}
INTEGRITY_ORIGINS = {
    "fiber_bundle." + module
    for module in (
        "cloud_files",
        "config",
        "search",
        "note_fingerprint_entries",
        "note_index",
        "note_search_cache",
        "note_search_inventory",
        "note_identity_store",
        "note_publications",
        "note_migration_store",
        "query_routing_files",
        "vaults",
        "vault_scope",
        "channels.evidence_passages",
    )
}


def retry_count(data: dict[str, Any]) -> int:
    saved = data.get(RETRY_KEY)
    if saved is None:
        return 0
    if (
        not isinstance(saved, dict)
        or set(saved) != {"schema", "attempts", "error_code"}
        or saved["schema"] != RETRY_SCHEMA
        or type(saved["attempts"]) is not int
        or saved["attempts"] != 1
        or saved["error_code"] not in CLOUD_RETRY.values()
    ):
        raise InvalidInput("channel_model_result_unknown")
    return 1


def defer_evidence(data: dict[str, Any], error: Exception, phase: str, source: Any) -> bool:
    """Call only before an answer is admitted; caller persists before returning."""
    if (
        phase != "evidence"
        or source is not None
        or not isinstance(error, IntegrityError)
        or str(error) not in CLOUD_RETRY
        or retry_count(data)
    ):
        return False
    data[RETRY_KEY] = {
        "schema": RETRY_SCHEMA,
        "attempts": 1,
        "error_code": CLOUD_RETRY[str(error)],
    }
    return True


def integrity(error: Exception) -> dict[str, str]:
    """Never retain arbitrary exception messages, source paths, or provider text."""
    if not isinstance(error, IntegrityError):
        return {}
    reason = str(error)
    diagnostic = {
        "integrity_reason": reason if reason in INTEGRITY_CODES else "unclassified_integrity_error"
    }
    frame = error.__traceback__
    while frame is not None:
        module = frame.tb_frame.f_globals.get("__name__")
        if module in INTEGRITY_ORIGINS:
            diagnostic["integrity_origin"] = module + ":" + str(frame.tb_lineno)
        frame = frame.tb_next
    return diagnostic


def answer(code: str, phase: str, data: dict[str, Any]) -> str:
    prefix = "本次知识库问答未完成（" + code + "；阶段：" + phase + "）。"
    if code in ANSWER_FORMAT_MESSAGES:
        return prefix + ANSWER_FORMAT_MESSAGES[code] + "本次未发送答案，未自动重试。"
    message = MESSAGES.get(code)
    if message is None:
        return prefix + "未自动重试，请在消息入口检查任务与模型配置。"
    if data.get(RETRY_KEY):
        message += "已完成一次本地重查，未重复请求检索模型。"
    return prefix + message + "请在本机确认同步、来源和索引状态后发起新查询。"
