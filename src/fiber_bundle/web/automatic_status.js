// One presentation contract for automatic tasks on home, inbox and task history.
const states = {
  awaiting_review: ["待确认保存", "waiting", true],
  index_reconcile: ["索引待核对", "error", true],
  failed: ["处理失败", "error", true],
  reconcile: ["待核对", "error", true],
  needs_review: ["待核对", "waiting", true],
  needs_supplement: ["待补全", "waiting", true],
  waiting_budget: ["等待预算", "waiting", true],
  waiting_dependency: ["等待处理环境", "waiting", true],
  superseded: ["已由后续任务接替", "info", false],
  complete: ["已完成", "success", false],
  cancelled: ["已取消", "info", false],
};
export function automaticStatus(job) {
  const state = job.superseded_by
    ? "superseded"
    : job.display_state ||
      (job.state === "reviewed" ? "awaiting_review" : job.state);
  const [text, tone, actionRequired] = states[state] || [state, "info", false];
  return {
    state,
    text:
      state === "index_reconcile" && job.publication_state === "published"
        ? "已保存 · 索引待核对"
        : text,
    tone,
    actionRequired,
  };
}
