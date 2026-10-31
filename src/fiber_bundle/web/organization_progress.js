// Derived display state only. Old stored jobs need no migration or re-evaluation.
export const modeNames = {
  quick: "快速整理",
  additive: "全量补充",
  deep: "深度重整",
};
export const jobNames = {
  queued: "排队中",
  running: "处理中",
  preview: "预览待确认",
  completed: "完成",
  partial: "部分完成",
  failed: "失败",
  blocked: "处理暂停",
  cancelled: "已取消",
  paused: "已暂停",
  cancelling: "撤销中",
  rollback_conflict: "撤销待核对",
  waiting_task: "等待原任务",
  waiting_file: "等待文件就绪",
  closed: "已结束 · 保留结果",
};
export function evidenceFailure(reason) {
  const messages = {
    format: "模型返回的分类格式不符合要求",
    moc_not_allowed: "模型选择了本次候选范围之外的 MOC",
    body_quote_count: "正文证据不足两条独立引文",
    body_quote_missing: "正文引文无法匹配本次发送的原文片段",
    body_quote_overlap: "正文引文重复或相互重叠",
    scope_quote_missing: "MOC 主题引文无法匹配所选 MOC 的主题定义",
  };
  return Object.hasOwn(messages, reason) ? messages[reason] : null;
}
export function manuallyResolved(item) {
  return (
    item.manual_resolution?.state === "resolved" &&
    item.manual_resolution.method === "manual_moc"
  );
}
export function itemCategory(item, job) {
  if (manuallyResolved(item)) return "processed";
  if (["waiting_file", "waiting_archive"].includes(item.state))
    return "waiting";
  if (item.error || item.analysis_error) return "error";
  if (job.stage === "planning" && !item.evaluated) return "waiting";
  if (item.status === "review") return "review";
  if (item.add?.length || item.remove?.length || item.target) return "changed";
  return "unchanged";
}
export function maintenanceSummary(job) {
  const items = job.items || [],
    counts = {
      changed: 0,
      review: 0,
      waiting: 0,
      error: 0,
      unchanged: 0,
      processed: 0,
    },
    pending = items.filter((i) => !manuallyResolved(i));
  for (const item of items) counts[itemCategory(item, job)]++;
  return {
    ...counts,
    total: items.length,
    evaluated: items.filter((i) => i.evaluated).length,
    done: items.filter((i) => i.state === "done" || manuallyResolved(i)).length,
    additions: pending.reduce((n, i) => n + (i.add?.length || 0), 0),
    removals: pending.reduce((n, i) => n + (i.remove?.length || 0), 0),
    manual: pending.filter((i) =>
      i.remove?.some((id) => (i.manual || []).includes(id)),
    ).length,
  };
}
export function previewInvalidated(job) {
  return (
    job.state === "preview" && job.preview_validity?.state === "invalidated"
  );
}
export function orderedMaintenance(jobs) {
  const active = (job) =>
    !["completed", "cancelled", "closed"].includes(job.state) &&
    !previewInvalidated(job);
  return jobs
    .filter((j) => j.maintenance)
    .slice()
    .sort(
      (a, b) =>
        Number(active(b)) - Number(active(a)) ||
        (b.created_at || 0) - (a.created_at || 0),
    );
}
export function mergeProgress(previous, incoming) {
  return {
    ...previous,
    ...incoming,
    jobs: incoming.jobs.map((job) => {
      const prior = previous.jobs.find((item) => item.id === job.id);
      return job.state === "preview" &&
        prior?.state === "preview" &&
        job.plan_sha256 === prior.plan_sha256 &&
        !job.preview_validity
        ? { ...job, preview_validity: prior.preview_validity }
        : job;
    }),
  };
}
export function effectiveIndexState(job) {
  const observed = job.index_observation;
  if (!observed || observed.state === "unknown") return job.index_state;
  if (observed.state !== "ready" || observed.after_task) return observed.state;
  return job.index_state;
}
export function maintenancePhase(job) {
  if (job.state === "closed") return "已结束 · 已完成结果保留";
  if (job.state === "waiting_file") return "部分已完成 · 等待文件就绪";
  if (job.blocking_task || job.state === "waiting_task")
    return "等待原任务处理";
  if (job.pause_requested) return "正在暂停 · 等待当前安全步骤完成";
  if (job.state === "cancelled")
    return job.rollback_completed
      ? "已取消 · 本次变更已撤销"
      : "已取消 · 未应用变更";
  if (job.stage === "rollback") return "撤销本次整理变更";
  if (previewInvalidated(job)) return "预览已失效";
  if (job.state === "preview") return "差异预览 · 等待确认";
  if (["partial", "failed", "blocked"].includes(job.state)) return "整理已中断";
  if (job.state === "paused") return "整理已暂停";
  if (job.stage === "planning") return "分析与生成预览";
  if (["queued", "running"].includes(job.state)) return "应用分类与归档";
  if (effectiveIndexState(job) === "pending") return "更新索引";
  if (effectiveIndexState(job) === "failed") return "索引更新失败";
  return job.state === "completed" ? "整理完成" : "应用分类与归档";
}
