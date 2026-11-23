// Index conflicts describe source state, rather than message-event deduplication.
export function noteIndexErrorText(code) {
  if (code?.startsWith("vault_sync_")) return indexProblem(code);
  return {
    layout_tracked_note_replaced:
      "普通笔记或 Map 的文件身份已变化。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置并保留原身份，再更新索引；无需重新入库。",
    layout_rebind_required:
      "部分已发布笔记已移动、改名、替换或缺失。请到知识库的“整理与布局迁移 → 批量核对外部改动”中核对位置后，再更新索引。",
    note_index_update_required: "笔记索引已过期，请更新此知识库索引。",
    placement_reconciliation_required:
      "等待操作核验。请先在知识库整理面板核对并收尾原操作；若为已提交归档，可在原整理任务选择“仅核验已提交归档”。",
  }[code];
}

// Shared by all themes: an accepted job is not a completed index.
import { locale } from "./i18n/index.js";
const choose = (zh, en) => (locale() === "en" ? en : zh);
export function noteIndexOutcome(result) {
  if (result?.state === "blocked")
    return indexDependencyText(result.dependency);
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
    publication_location_database_changed: [
      "资料登记数据库已更换，请刷新状态后重新核对原操作，不要直接重试写入。",
      "The publication registry database was replaced. Refresh state and review the original operation before any further writes.",
    ],
    placement_reconciliation_required: [
      "原位置调整操作尚未收尾。请核对原操作并仅核验已提交归档，保留已提交结果；任务保持中断，核验后索引自动继续。",
      "An original placement operation is unfinished. Review it and verify only committed archives while retaining committed results. The task stays stopped; indexing continues after verification.",
    ],
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
    vault_sync_revision_conflict: [
      "索引观察修订与保留的文件意图冲突。原记录已保留，请查看索引诊断并核对观察代次；不要重新入库。",
      "The index observation revision conflicts with a retained file intent. Records are kept. Review index diagnostics and observation generations; do not re-import.",
    ],
    vault_sync_identity_conflict: [
      "文件身份与登记不一致。请先核对外部移动或替换，保留原身份与历史。",
      "File identity differs from its registration. Review external moves or replacements while retaining the original identity and history.",
    ],
    vault_sync_location_changed: [
      "文件位置已变化。请核对外部改动并保留原登记，再更新索引。",
      "The file location changed. Reconcile external changes while keeping its registration, then update the index.",
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
    "索引检查未完成，具体原因尚未识别。请保留原文件并查看诊断代码。",
    "The index check did not finish and its cause is unrecognized. Keep the source files and review the diagnostic code.",
  );
}
export function indexIsRunning(value) {
  if (value?.sync) return ["pending", "building"].includes(value.state);
  return ["pending", "building"].includes(value?.update?.state);
}

export function indexDependencyTitle(dependency) {
  return dependency?.recovery === "verify_committed_archives"
    ? choose("等待归档核验", "Waiting for archive verification")
    : choose("等待操作核验", "Waiting for operation review");
}
export function indexDependencyText(dependency) {
  const operations = (dependency?.operations || []).map(
    (operation) =>
      `${choose("操作", "Operation")} ${operation.id} · ${operation.kind}${operation.job_ids?.length ? ` · ${choose("任务", "Task")} ${operation.job_ids.join(", ")}` : ""}`,
  );
  return [
    indexDependencyTitle(dependency),
    ...operations,
    ...(dependency?.owner_lookup_failed
      ? [
          choose(
            "所属任务暂无法读取；原操作仍阻塞索引。",
            "The owning task could not be read; the original operation still blocks indexing.",
          ),
        ]
      : []),
    dependency?.recovery === "verify_committed_archives"
      ? choose(
          "在对应归档整理任务中选择“仅核验已提交归档”。此操作保留任务中断状态，不处理其余条目；核验解除阻塞后索引自动继续。其他种类或无所属任务的操作请到整理与布局迁移核对原记录。",
          "For the owning archive task, choose “Verify committed archives only”. The task stays stopped and remaining items are retained. Indexing continues when blockers clear. For other operation kinds or operations without an owning task, review the original record in Organize & migrate.",
        )
      : choose(
          "请到知识库的整理与布局迁移中核对原操作；保留现有记录。所有阻塞解除后索引自动继续。",
          "Review the original operation in the vault’s Organize & migrate panel and retain its records. Indexing continues when all blockers clear.",
        ),
  ].join(" · ");
}
