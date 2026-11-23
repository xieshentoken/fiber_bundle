import { locale, msg, uiText } from "./i18n/index.js";
// Explicit lifecycle and model actions. Rendering never starts a task by itself.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import {
  manuallyResolved,
  effectiveIndexState,
} from "./organization_progress.js";

export function archiveResumeNeedsReview(job) {
  return (
    !["closed", "completed", "cancelled"].includes(job.state) &&
    job.confirmed &&
    job.items?.some(
      (i) =>
        i.archive_operation &&
        !i.archive_deferral &&
        i.phase === "members_done" &&
        (["placement_preview_not_ready", "placement_preview_expired"].includes(
          i.error,
        ) ||
          ["placement_preview_not_ready", "placement_preview_expired"].includes(
            job.error,
          )),
    )
  );
}

export function taskActions(job) {
  const actions = [];
  if (job.pause_requested) return actions;
  if (job.archive_verification?.available)
    actions.push({
      operation: "verify_committed_archives",
      label:
        locale() === "en"
          ? "Verify committed archives only"
          : "仅核验已提交归档",
    });
  if (
    job.cache_recovery &&
    !job.confirmed &&
    job.state === "preview" &&
    job.items.some(
      (i) =>
        !manuallyResolved(i) &&
        i.analysis_error === "organization_model_cache_incomplete",
    )
  )
    actions.push({
      operation: "supplement_missing",
      label: "仅补齐缺少的模型分析",
      consent: true,
    });
  if (
    !job.pause_requested &&
    ["queued", "running", "preview", "cancelling"].includes(job.state)
  )
    actions.push({ operation: "pause", label: "暂停整理" });
  if (
    !job.blocking_task &&
    !archiveResumeNeedsReview(job) &&
    [
      "failed",
      "partial",
      "blocked",
      "paused",
      "waiting_task",
      "waiting_file",
    ].includes(job.state)
  )
    actions.push({ operation: "resume", label: "恢复未完成整理" });
  if (job.state === "rollback_conflict")
    actions.push({ operation: "resume", label: "核对后继续撤销" });
  if (effectiveIndexState(job) === "failed" && job.state === "completed")
    actions.push({ operation: "resume", label: "重试整理索引" });
  if (
    [
      "queued",
      "running",
      "preview",
      "blocked",
      "paused",
      "partial",
      "failed",
      "waiting_file",
      "waiting_task",
    ].includes(job.state)
  )
    actions.push({
      operation: "cancel",
      label: job.confirmed ? "取消并撤销本次整理" : "取消整理",
    });
  if (
    job.state === "preview" &&
    !job.cache_recovery &&
    !job.confirmed &&
    job.mode === "quick" &&
    job.items.some(
      (i) =>
        !manuallyResolved(i) &&
        !i.selected?.length &&
        !i.existing_reliable &&
        !i.analysis_error &&
        !i.model_used,
    )
  )
    actions.push({ operation: "supplement", label: "用模型补充未解决项" });
  if (
    ["preview", "blocked", "paused"].includes(job.state) &&
    !job.confirmed &&
    job.items.some(
      (i) =>
        !manuallyResolved(i) &&
        i.analysis_error === "organization_model_result_unknown",
    )
  )
    actions.push({
      operation: "retry_unknown",
      label: "重新调用结果未知项",
      consent: true,
    });
  return actions;
}

// Generic classification jobs keep business writeback separate from index-only recovery.
export function classificationActions(job) {
  const actions = [];
  if (job.state === "preview")
    actions.push({ operation: "confirm", label: "确认此预览" });
  if (["queued", "running", "preview"].includes(job.state))
    actions.push({ operation: "cancel", label: "取消剩余项" });
  if (["partial", "failed", "cancelled", "blocked"].includes(job.state))
    actions.push({
      operation: "resume",
      label:
        job.resume_mode === "cached_writeback"
          ? msg("organization.recovery.cached_resume")
          : "恢复未完成项",
    });
  else if (job.state === "completed" && job.index_state === "failed")
    actions.push({ operation: "resume", label: "重试索引更新" });
  return actions;
}

export function appendTaskControls(host, job, action) {
  if (job.state === "closed") return;
  if (archiveResumeNeedsReview(job))
    host.append(
      el(
        "p",
        uiText(
          "原归档预览尚不可执行，直接恢复无法更新原计划。请先选择“核对并暂缓受阻归档”，或核对后结束任务并保留结果。",
        ),
        "task-impact",
      ),
    );
  if (job.pause_requested)
    host.append(
      el(
        "p",
        uiText("暂停请求已接收；当前安全步骤完成后暂停，已完成修改会保留。"),
        "muted",
      ),
    );
  if (job.archive_verification?.available)
    host.append(
      el(
        "p",
        locale() === "en"
          ? "Verifies only this task’s already committed archive operations. The task stays stopped; remaining items and original errors are retained. No model calls."
          : "仅核验本任务已提交的归档操作。任务保持中断，其余条目及原错误保留；不调用模型。",
        "task-impact",
      ),
    );
  for (const receipt of job.archive_verification?.receipts || [])
    host.append(el("p", archiveVerificationText(receipt), "task-impact"));
  for (const spec of taskActions(job)) {
    const button = control(spec.label, action(spec.operation));
    if (spec.consent) {
      const checkbox = el("input"),
        label = el(
          "label",
          spec.operation === "supplement_missing"
            ? uiText("允许仅向当前模型发送尚无缓存的片段，沿用统一模型消耗。")
            : uiText("我已核对调用记录，接受再次调用可能产生额外消耗。"),
        );
      checkbox.type = "checkbox";
      label.prepend(checkbox);
      button.disabled = true;
      checkbox.addEventListener("change", () => {
        button.disabled = !checkbox.checked;
      });
      host.append(label);
    }
    host.append(button);
  }
  if (
    job.state === "preview" &&
    job.mode === "quick" &&
    job.items.some((i) => !manuallyResolved(i))
  )
    host.append(
      el(
        "p",
        job.cache_recovery
          ? uiText(
              "已有窗口继续复用；缺少的片段需明确确认后补齐，超时项保持原状。",
            )
          : uiText(
              "默认使用本地规则与有效缓存。模型补充会发送未解决项正文，沿用当前模型与统一消耗配置。",
            ),
        "muted",
      ),
    );
}

export function unknownOutcome(detail) {
  const when = detail?.created_at
    ? new Date(detail.created_at * 1000).toLocaleString("zh-CN") + "的"
    : "此前的";
  const reason =
    detail?.reason === "model_timeout"
      ? "模型调用超时，未取得结果"
      : "模型调用结果尚未确认";
  return `${when}${reason}。本项单独保留，其余项目继续；不会自动重复发送。`;
}

// A request remains owned by its task across polling-driven replacements.
export function maintenanceSubmission(draft, id) {
  draft.submissions ||= new Map();
  if (!draft.submissions.has(id)) draft.submissions.set(id, { pending: null });
  return draft.submissions.get(id);
}
export function setMaintenanceSubmission(state, operation) {
  state.pending = operation;
  for (const binding of state.controls || []) {
    if (operation && !binding.locked) {
      binding.disabled = binding.node.disabled;
      binding.locked = true;
    }
    if (operation) binding.node.disabled = true;
    else if (binding.locked) {
      binding.node.disabled = binding.disabled;
      binding.locked = false;
    }
  }
  if (state.feedback) {
    state.feedback.hidden = !operation;
    // The request is known; its internal backend phase is not.
    state.feedback.replaceChildren(
      el(
        "span",
        msg(
          operation === "confirm"
            ? "inbox_review.confirm_pending"
            : "inbox_review.operation_pending",
        ),
      ),
    );
  }
}
export function bindMaintenanceSubmission(host, state) {
  state.controls = Array.from(host.querySelectorAll("button,input")).map(
    (node) => ({ node, locked: false }),
  );
  state.feedback = el("p", null, "task-impact");
  state.feedback.setAttribute("role", "status");
  host.append(state.feedback);
  setMaintenanceSubmission(state, state.pending);
}

export function archiveVerificationText(receipt) {
  const en = locale() === "en";
  const states = {
    verified: en ? "Verified; task remains stopped" : "核验完成；任务保持中断",
    conflict: en
      ? "Conflict; review the original operation"
      : "存在冲突；请核对原操作",
    unknown: en
      ? "Outcome unconfirmed; no automatic retry"
      : "结果尚未确认；不会自动重试",
    verifying: en
      ? "Verification outcome pending; refresh status"
      : "核验结果待确认；请刷新状态",
  };
  return `${en ? "Archive verification receipt" : "归档核验回执"} · ${states[receipt.state] || receipt.state} · ${receipt.operations.join(", ")}${receipt.error ? " · " + receipt.error : ""}${receipt.detail ? " · " + [receipt.detail.reason, receipt.detail.phase, receipt.detail.path, ...(receipt.detail.changed_fields || [])].filter(Boolean).join(" · ") : ""}`;
}
