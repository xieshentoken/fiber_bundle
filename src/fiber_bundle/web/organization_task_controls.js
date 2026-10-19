import { uiText } from "./i18n/index.js";
// Explicit lifecycle and model actions. Rendering never starts a task by itself.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { manuallyResolved } from "./organization_progress.js";

export function taskActions(job) {
  const actions = [];
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
  if (["queued", "running", "preview", "cancelling"].includes(job.state))
    actions.push({ operation: "pause", label: "暂停整理" });
  if (["failed", "partial", "blocked", "paused"].includes(job.state))
    actions.push({ operation: "resume", label: "恢复未完成整理" });
  if (job.state === "rollback_conflict")
    actions.push({ operation: "resume", label: "核对后继续撤销" });
  if (job.index_state === "failed" && job.state === "completed")
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

export function appendTaskControls(host, job, action) {
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
