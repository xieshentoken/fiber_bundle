// One display model feeds native progress attributes and adjacent text in both hosts.
import { el } from "./ui.js";
import { phrase, setText, uiText } from "./i18n/index.js";
import { archiveFinalizationVerified } from "./organization_progress.js";
import { taskEnded } from "./organization_task_record.js";

export function executionProgress(job) {
  const packet = job.execution_progress;
  if (
    packet?.schema === "organization-progress/v1" &&
    Number.isSafeInteger(packet.completed) &&
    packet.completed >= 0 &&
    Number.isSafeInteger(packet.total) &&
    packet.total >= packet.completed &&
    [
      "analyzed",
      "processed",
      "mounted",
      "archived",
      "waiting",
      "review",
      "archive_pending",
    ].every(
      (key) =>
        Number.isSafeInteger(packet.counts?.[key]) &&
        packet.counts[key] >= 0 &&
        packet.counts[key] <= packet.total,
    )
  )
    return packet.phase === "finalizing" && archiveFinalizationVerified(job)
      ? { ...packet, phase: "applying" }
      : packet;
  const items = job.items || [];
  const counts = {
    analyzed: items.filter((item) => item.evaluated).length,
    processed: Number.isSafeInteger(job.completed)
      ? job.completed
      : items.filter(
          (item) =>
            item.state === "done" ||
            item.manual_resolution?.state === "resolved",
        ).length,
    mounted: items.filter(
      (item) => item.membership_observation?.state === "current",
    ).length,
    archived: items.filter((item) => item.phase === "archived").length,
    waiting: items.filter((item) =>
      ["waiting_file", "waiting_archive"].includes(item.state),
    ).length,
    review: items.filter((item) => item.status === "review").length,
    archive_pending: items.filter(
      (item) =>
        item.archive_operation &&
        item.phase !== "archived" &&
        !item.archive_wait &&
        !item.archive_deferral,
    ).length,
  };
  const phase =
    job.stage === "rollback"
      ? "rollback"
      : job.stage === "planning" || job.state === "preview"
        ? job.state === "preview"
          ? "preview"
          : "analysis"
        : "applying";
  return {
    phase,
    counts,
    completed:
      phase === "rollback"
        ? job.rollback_progress || 0
        : ["analysis", "preview"].includes(phase)
          ? counts.analyzed
          : counts.processed,
    total: phase === "rollback" ? job.rollback_total || 0 : items.length,
  };
}

export function appendExecutionProgress(host, job) {
  const value = executionProgress(job),
    planning = ["analysis", "preview"].includes(value.phase),
    rollback = value.phase === "rollback",
    ended = taskEnded(job),
    label = ended
      ? "历史处理记录"
      : rollback
        ? "撤销进度"
        : planning
          ? "分析进度"
          : "应用进度";
  const text = ended
    ? phrase("历史处理记录：{p0} / {p1} 项；任务已结束。", {
        p0: value.completed,
        p1: value.total,
      })
    : rollback
      ? phrase("撤销本次变更 · 已核对 {p0} / {p1} 个文件 · 保留用户后续修改", {
          p0: value.completed,
          p1: value.total,
        })
      : phrase("{p0} {p1} / {p2} 项{p3}", {
          p0: uiText(planning ? "已分析" : "已处理"),
          p1: value.completed,
          p2: value.total,
          p3: uiText(planning ? " · 尚未应用本次变更" : ""),
        });
  const progress = el("progress", null, "task-progress");
  // Set attributes too: native HTMLProgressElement reflects these values. In
  // particular, textContent/value never carries an invented percentage.
  progress.max = Math.max(value.total, 1);
  progress.value = value.completed;
  setText(progress, uiText(label), "aria-label");
  setText(progress, text, "aria-valuetext");
  host.append(el("p", text), progress);
  if (job.state === "preview" && value.total > 0)
    host.append(
      el("p", uiText("分析已完成，等待确认；尚未应用或归档。"), "muted"),
    );
}
