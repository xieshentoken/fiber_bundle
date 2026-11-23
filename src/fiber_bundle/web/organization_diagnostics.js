// Translate known evidence at the display boundary; raw paths and operation IDs stay raw.
import { uiText, phrase } from "./i18n/index.js";
import { archiveFinalizationVerified } from "./organization_progress.js";
import { requestErrors } from "./request_errors.js";

const relativePath = (value) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= 2048 &&
  !value.startsWith("/") &&
  !value.split("/").includes("..");
const retainedLimit = (value) =>
  value?.reason === "retained_evidence_limit" &&
  value.phase === "moc_retained_members";

function retainedEvidenceFields(value) {
  return {
    ...(typeof value.moc_id === "string" &&
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.moc_id)
      ? { moc_id: value.moc_id }
      : {}),
    ...Object.fromEntries(
      ["actual_bytes", "limit_bytes"]
        .filter((key) => Number.isSafeInteger(value[key]) && value[key] >= 0)
        .map((key) => [key, value[key]]),
    ),
    ...(Array.isArray(value.missing_members)
      ? {
          missing_members: value.missing_members
            .slice(0, 20)
            .filter(relativePath),
        }
      : {}),
  };
}

function retainedEvidenceFacts(value) {
  const detail = retainedEvidenceFields(value),
    facts = [];
  if (detail.moc_id) facts.push(phrase("MOC ID：{p0}", { p0: detail.moc_id }));
  if (detail.actual_bytes != null && detail.limit_bytes != null)
    facts.push(
      phrase("校验证据：{p0} 字节 · 上限 {p1} 字节", {
        p0: detail.actual_bytes,
        p1: detail.limit_bytes,
      }),
    );
  for (const path of detail.missing_members || [])
    facts.push(phrase("缺失成员：{p0}", { p0: path }));
  return facts;
}

export function historicalItemFailure(item) {
  return phrase("历史错误：{p0}；任务已结束，已完成结果保留。", {
    p0: item.error || item.analysis_error,
  });
}

export function readObservationDiagnostic(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (
    !["reason", "phase"].every(
      (key) =>
        typeof value[key] === "string" && /^[a-z_]{1,100}$/.test(value[key]),
    )
  )
    return null;
  if (
    value.path != null &&
    (typeof value.path !== "string" ||
      value.path.length > 2048 ||
      value.path.startsWith("/") ||
      value.path.split("/").includes(".."))
  )
    return null;
  return {
    reason: value.reason,
    phase: value.phase,
    ...(value.path != null ? { path: value.path } : {}),
    changed_fields: Array.isArray(value.changed_fields)
      ? value.changed_fields
          .filter(
            (field) =>
              typeof field === "string" && /^[a-z][a-z0-9_]{0,39}$/.test(field),
          )
          .slice(0, 9)
      : [],
    retryable_read: value.retryable_read === true,
    ...(retainedLimit(value) ? retainedEvidenceFields(value) : {}),
  };
}

export function organizationDiagnostic(job) {
  if (job.state === "closed") return null;
  const historical = archiveFinalizationVerified(job),
    detail = job.error_detail || {},
    item = (job.items || []).find(
      (entry) => entry.archive_operation && entry.phase !== "archived",
    ),
    operation =
      detail.operation_id ||
      detail.operation ||
      item?.archive_operation ||
      (historical
        ? [
            ...new Set(
              job.items.map((entry) => entry.archive_operation).filter(Boolean),
            ),
          ].join(" · ")
        : undefined);
  let text;
  if (historical)
    text =
      "原归档错误已核验并完成收尾，以下为历史诊断。整理任务保持原状态，剩余条目不会自动继续。";
  else if (
    !job.confirmed &&
    job.state === "preview" &&
    (["directory_changed", "scope_changed"].includes(detail.reason) ||
      [
        "note directory changed",
        "organization_directory_observation_changed",
        "placement_link_sources_changed",
      ].includes(job.error))
  )
    text =
      "预览核对未通过，尚未执行整理。请检查具体文件或目录变化，再按当前资料重新生成预览。";
  else if (job.error === "moc_retained_member_limit")
    text = requestErrors.moc_retained_member_limit[0];
  else if (detail.reason === "committed_file_changed")
    text =
      "已提交的目标文件或交换备份发生变化，归档收尾已停止。请核对列出的文件、变化字段和原操作记录；不要重新创建归档或重放写入。";
  else if (detail.reason === "scope_changed")
    text =
      "父目录、访问范围或符号链接发生变化，当前核对已停止。请核对列出的路径、变化字段和权限；不要以等待同步或直接重试写入处理。";
  else if (job.error === "placement_committed_evidence_missing")
    text =
      "原操作的已提交写入证据不完整，不能继续收尾。请保留文件并核对原操作记录，不要重新创建归档或重放写入。";
  else if (job.error === "publication_location_database_changed")
    text =
      "资料登记数据库已更换，当前核对已停止。请刷新状态后重新核对原操作，不要直接重试写入。";
  else if (
    ["archive_committed_verify", "archive_finalize_inventory"].includes(
      detail.phase,
    )
  )
    text =
      "原操作已写入，归档收尾核对尚未完成。核对原操作记录后恢复收尾，不要重新创建归档。";
  else if (
    detail.reason === "directory_changed" ||
    job.error === "note directory changed" ||
    job.error === "organization_directory_observation_changed"
  )
    text =
      "目录观察在核对期间变化，当前步骤已停止。请核对列出的目录与变化字段，再读取状态或恢复原任务。";
  else if (job.error === "maintenance_membership_baseline_changed")
    text =
      "共享 MOC 的当前投影与原任务依据不一致。请核对该 MOC 及原操作记录，保留已完成结果；不要重复挂载。";
  else if (job.blocking_task || job.state === "waiting_task")
    text =
      "同一知识库的原任务尚未收尾。请查看阻塞任务及其写入记录，再决定恢复或保留结果结束。";
  else if (job.items?.some((entry) => entry.archive_wait))
    text =
      "归档因链接或依赖核对暂缓。请查看条目中的文件、链接与行号；原路径和可靠挂载保留。";
  else if (job.state === "waiting_file")
    text =
      "原文尚未在本机就绪。可准备本任务等待资料，或先在 Finder 核对同步状态。";
  if (!text) return null;
  return {
    text: uiText(text),
    ...(historical
      ? {
          historical: true,
          facts: [phrase("历史错误：{p0}", { p0: job.error })],
        }
      : {}),
    path: detail.path,
    phase: detail.phase,
    fields: detail.changed_fields || [],
    operation,
    ...(job.error === "moc_retained_member_limit"
      ? { facts: retainedEvidenceFacts(detail) }
      : {}),
  };
}
