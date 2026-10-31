// Index conflicts describe source state, rather than message-event deduplication.
export function noteIndexErrorText(code) {
  return {
    layout_tracked_note_replaced:
      "普通笔记或 Map 的文件身份已变化。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置并保留原身份，再更新索引；无需重新入库。",
    layout_rebind_required:
      "部分已发布笔记已移动、改名、替换或缺失。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置后，再更新索引。",
    note_index_update_required: "笔记索引已过期，请更新此知识库索引。",
    placement_reconciliation_required:
      "存在尚未完成的位置调整，请先在知识库整理面板恢复该操作，再更新索引。",
  }[code];
}

// Shared by all themes: an accepted job is not a completed index.
import { locale } from "./i18n/index.js";
const choose = (zh, en) => (locale() === "en" ? en : zh);
export function noteIndexOutcome(result) {
  if (result?.state === "ready")
    return choose("索引更新完成。", "Index updated.");
  if (result?.state === "partial")
    return choose(
      "可用笔记已更新；部分笔记等待同步或核对。",
      "Available notes updated; some notes await sync or review.",
    );
  return choose(
    "更新任务已提交，后台将分批处理。可离开此页面，在知识库的索引面板查看进度。",
    "Index update queued. You can leave this page and check progress in the vault's index panel.",
  );
}
export function indexProblem(code) {
  const text = {
    vault_sync_cloud_pending: [
      "文件尚未下载；在 Finder 中下载后可重试。",
      "File is not downloaded. Download it in Finder, then retry.",
    ],
    vault_sync_cloud_metadata_unavailable: [
      "iCloud 尚未确认文件版本；保留本地文件，等待同步后重试。",
      "iCloud has not confirmed the file version. Keep the local file and retry after sync.",
    ],
    vault_sync_cloud_timeout: [
      "iCloud 状态检查超时；后台会退避重试。",
      "iCloud status check timed out; the backend will retry with backoff.",
    ],
    vault_sync_cloud_unconfirmed: [
      "iCloud 同步或冲突状态未通过校验；请在 Finder 核对。",
      "iCloud sync or conflict checks did not pass. Check the file in Finder.",
    ],
    vault_sync_index_capacity: [
      "本地索引达到容量限制；旧索引保留，需要检查索引容量。",
      "The local index reached its capacity limit. The previous index is retained.",
    ],
    vault_sync_storage_busy: [
      "本地数据库暂忙；后台将重试。",
      "Local storage is busy; the backend will retry.",
    ],
    vault_sync_index_busy: [
      "已有索引任务正在执行。",
      "An index task is already running.",
    ],
    vault_sync_source_changed: [
      "文件仍在变化，等待保存稳定后重试。",
      "The file is changing. Retry after the save settles.",
    ],
    vault_sync_metadata_changed: [
      "登记信息发生变化，后台将重新核对。",
      "Registration changed; the backend will recheck it.",
    ],
    vault_sync_index_database_error: [
      "索引数据库更新失败；旧索引保留，请查看后台诊断。",
      "Index database update failed. The previous index is retained; check backend diagnostics.",
    ],
  }[code];
  if (text) return choose(...text);
  if (/identity|review|location|revision|scope|root/.test(code || ""))
    return choose(
      "文件身份或位置需要核对；保留原登记，不会自动覆盖。",
      "File identity or location needs review. The original registration will not be overwritten.",
    );
  return choose(
    "文件暂不可用；其余笔记继续更新，后台稍后重试。",
    "The file is temporarily unavailable. Other notes continue updating; the backend will retry.",
  );
}
export function indexIsRunning(value) {
  if (value?.sync) return ["pending", "building"].includes(value.state);
  return ["pending", "building"].includes(value?.update?.state);
}
