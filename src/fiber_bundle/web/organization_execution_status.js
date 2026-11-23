// Current observations are separate from stored task outcomes. Rendering has no side effects.
import { el } from "./ui.js";
import { phrase, uiText, joinText, sourceText } from "./i18n/index.js";
import {
  archiveFinalizationVerified,
  effectiveIndexState,
} from "./organization_progress.js";
import { organizationDiagnostic } from "./organization_diagnostics.js";
import {
  indexDependencyText,
  indexDependencyTitle,
  indexProblem,
} from "./note_index_errors.js";

const reasons = {
  stale: "iCloud 文件版本暂未稳定，当前步骤已停止。",
  metadata_unavailable: "暂时无法核对 iCloud 元数据，当前步骤已停止。",
  metadata_changed: "文件状态在核对期间变化，当前步骤已停止。",
  conflict: "iCloud 存在版本冲突，请先核对文件。",
  pending: "文件尚未下载到本机，当前步骤已停止。",
};
export function executionStatusModel(job) {
  const items = job.items || [],
    historical = archiveFinalizationVerified(job);
  return {
    interrupted:
      !job.blocking_task &&
      ["partial", "failed", "blocked"].includes(job.state),
    failed: items.filter((i) => i.state === "failed" || i.state === "blocked")
      .length,
    remaining: items.filter((i) => i.state === "pending").length,
    failedPath:
      (!historical && job.error_detail?.path) ||
      items.find((i) => i.state === "failed")?.path,
    reason:
      !historical && Object.hasOwn(reasons, job.error_detail?.reason)
        ? reasons[job.error_detail.reason]
        : null,
    indexState: effectiveIndexState(job),
    lastProgress:
      job.last_progress_at || job.updated_at || job.activity?.updated_at,
  };
}
export function appendExecutionStatus(host, job, explain) {
  const value = executionStatusModel(job);
  const diagnostic = organizationDiagnostic(job);
  if (value.interrupted)
    host.append(
      el(
        "p",
        phrase("已停止：{p0} 项失败，{p1} 项待处理；不会自动继续。", {
          p0: value.failed,
          p1: value.remaining,
        }),
        "task-impact",
      ),
    );
  if (diagnostic) {
    host.append(
      el("p", diagnostic.text, diagnostic.historical ? "muted" : "task-impact"),
    );
    const facts = [
      diagnostic.path,
      diagnostic.phase,
      ...diagnostic.fields,
      diagnostic.operation,
      ...(diagnostic.facts || []),
    ].filter(Boolean);
    if (facts.length) host.append(el("p", joinText(facts, " · "), "muted"));
  }
  if (job.error && !job.blocking_task && !diagnostic && job.state !== "closed")
    host.append(
      el(
        "p",
        uiText(value.reason || explain(job.error, job.error_detail)),
        "task-failure",
      ),
    );
  if (value.failedPath)
    host.append(
      el(
        "p",
        joinText([
          uiText(
            !diagnostic?.historical && job.error_detail?.path
              ? "核对失败的文件"
              : "中断条目",
          ),
          " · ",
          sourceText(value.failedPath),
        ]),
        "muted",
      ),
    );
  if (
    job.error === "cloud_item_pending_stale_or_conflicted" &&
    !job.error_detail
  )
    host.append(
      el(
        "p",
        uiText("旧记录未保存具体文件和变化类型；以上为中断时处理的条目。"),
        "muted",
      ),
    );
  if (job.recovery_error)
    host.append(
      el(
        "p",
        uiText("恢复证据也需核对；已保留主错误和现有文件。"),
        "task-impact",
      ),
    );
  if (value.lastProgress)
    host.append(
      el(
        "p",
        phrase("最后进展：{p0}", {
          p0: new Date(value.lastProgress * 1000).toLocaleString(),
        }),
        "muted",
      ),
    );
  if (value.indexState)
    host.append(
      el(
        "p",
        phrase("知识库索引：{p0}", {
          p0:
            value.indexState === "blocked"
              ? indexDependencyTitle(
                  job.index_observation?.dependency || job.index_dependency,
                )
              : uiText(
                  {
                    ready: "已就绪",
                    pending: "等待更新",
                    building: "更新中",
                    blocked: "等待归档核验",
                    waiting: "等待文件或观察器就绪",
                    partial: "部分内容待核对",
                    stale: "需要更新",
                    failed: "更新失败，可单独重试",
                  }[value.indexState] || "状态待核对",
                ),
        }),
      ),
    );
  const dependency = job.index_observation?.dependency || job.index_dependency;
  if (dependency)
    host.append(el("p", indexDependencyText(dependency), "task-impact"));
  if (job.index_state === "failed" && value.indexState === "ready")
    host.append(
      el("p", uiText("索引已在之后恢复；整理任务仍保持原状态。"), "muted"),
    );
  if (job.index_observation?.error)
    host.append(
      el(
        "p",
        job.index_observation.error === "index_status_read_failed"
          ? uiText("索引状态读取失败；保留上次观察，请手动刷新。")
          : indexProblem(job.index_observation.error),
        "task-impact",
      ),
    );
}
