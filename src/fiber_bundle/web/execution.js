import { joinText, phrase, uiText } from "./i18n/index.js";
import { confirmAction } from "./confirm_dialog.js";
import { shortTime } from "./home_status.js";
import { bindingLabel, callState, runState, shortId } from "./state_labels.js";
// Execution presentation preserves state, fees and explicit cancellation/reconciliation.
import { $, action, el } from "./ui.js";
import { createUsageView } from "./usage.js";
import { modelBudgetSettings } from "./model_budget_settings.js";
import { money } from "./usage_chart.js";
import {
  control,
  showJSON,
  recordHeading,
  reconcileChildren,
} from "./record_ui.js";
function runId(id) {
  const line = el("p", null, "muted");
  const code = el(
    "span",
    phrase("任务 {p0}", { p0: shortId(id) }),
    "technical",
  );
  code.title = id;
  line.append(code);
  return line;
}
// Model calls as a table; amounts are the ledger values, unknown results stay explicit.
function callTable(calls) {
  const details = el("details", null, "technical-details");
  details.append(
    el("summary", phrase("模型请求与费用明细（{p0}）", { p0: calls.length })),
  );
  if (!calls.length) {
    details.append(el("p", uiText("尚无模型请求记录。"), "muted"));
    return details;
  }
  const table = el("table", null, "call-table"),
    head = el("tr");
  for (const text of ["模型", "状态", "预留", "已计费", "计费日"]) {
    const cell = el("th", uiText(text));
    cell.scope = "col";
    head.append(cell);
  }
  const thead = el("thead"),
    body = el("tbody");
  thead.append(head);
  for (const call of calls) {
    const row = el("tr"),
      [state, tone] = callState(call.state),
      badge = el("span", uiText(state), "status-badge " + tone),
      stateCell = el("td");
    stateCell.append(badge);
    row.append(
      el("td", call.model || uiText("未记录")),
      stateCell,
      el("td", money(call.reserved_nusd)),
      el("td", call.charged_nusd === null ? "—" : money(call.charged_nusd)),
      el("td", call.billing_day || "—"),
    );
    body.append(row);
  }
  table.append(thead, body);
  details.append(table);
  showJSON(details, calls, "完整账本记录");
  return details;
}
export function createExecutionView(api) {
  let epoch = 0;
  const records = new Map();
  const usage = createUsageView(api);
  const budgets = modelBudgetSettings(api);
  $("execution-content").before(budgets.root);
  async function refreshRuns() {
    const revision = ++epoch,
      data = await api.inventoryExecutions();
    if (revision !== epoch) return;
    const root = $("execution-content");
    const nodes = [],
      next = new Map();
    const focused = document.activeElement;
    const focusRun = focused?.closest?.("[data-run-id]")?.dataset.runId;
    const focusKey = focused?.dataset.focusKey;
    if (!data.runs.length)
      nodes.push(el("p", uiText("暂无执行任务或模型费用。")));
    for (const run of data.runs) {
      const signature = JSON.stringify(run),
        previous = records.get(run.id);
      if (previous?.signature === signature) {
        nodes.push(previous.card);
        next.set(run.id, previous);
        continue;
      }
      const [stateText, tone] = runState(run.state);
      const card = el("article", null, "row record");
      card.dataset.runId = run.id;
      card.append(
        recordHeading(
          joinText(
            [
              uiText(bindingLabel(run.binding_kind)),
              shortTime(run.updated_at || run.created_at),
            ],
            " · ",
          ),
          stateText,
          tone,
        ),
        runId(run.id),
        el(
          "p",
          phrase("任务上限 {p0} USD；尝试 {p1}", {
            p0: money(run.task_limit_nusd),
            p1: run.attempts,
          }),
        ),
      );
      if (run.error_code) card.append(el("p", run.error_code, "error"));
      const uncertain = run.calls.filter(
        (call) => call.state !== "settled",
      ).length;
      if (uncertain)
        card.append(
          el(
            "p",
            phrase("未结算或未知请求：{p0}；预留与原记录保留。", {
              p0: uncertain,
            }),
          ),
        );
      card.append(callTable(run.calls));
      card.querySelector("summary").dataset.focusKey = "costs";
      showJSON(card, run.calls, "技术详情");
      card.querySelector("details").open =
        previous?.card.querySelector("details").open || false;
      const actions = el("div", null, "record-actions");
      if (
        ["queued", "running", "waiting_budget", "interrupted"].includes(
          run.state,
        )
      )
        actions.append(
          control("取消当前执行", async () => {
            if (
              !(await confirmAction({
                title: "取消此执行？",
                body: "原件、已知费用和未知结果会保留。",
                confirmLabel: "取消执行",
                cancelLabel: "保留执行",
                tone: "danger",
              }))
            )
              return;
            await api.cancelExecution(run.id);
            await refresh();
          }),
        );
      if (run.state === "running")
        actions.append(
          control("对账中断执行", async () => {
            if (
              !(await confirmAction({
                title: "对账中断执行？",
                body: "仅对账已失去负责人的执行，未知请求不会重放。",
                confirmLabel: "开始对账",
              }))
            )
              return;
            await api.reconcileExecution(run.id);
            await refresh();
          }),
        );
      for (const button of actions.children)
        button.dataset.focusKey = button.textContent;
      if (actions.childElementCount) card.append(actions);
      nodes.push(card);
      next.set(run.id, { card, signature });
    }
    // Keep unchanged nodes in place so focus and expanded technical evidence survive.
    reconcileChildren(root, nodes);
    records.clear();
    for (const [id, item] of next) records.set(id, item);
    if (focusRun && focusKey && document.activeElement !== focused) {
      const card = records.get(focusRun)?.card;
      const target = Array.from(
        card?.querySelectorAll("[data-focus-key]") || [],
      ).find((node) => node.dataset.focusKey === focusKey);
      (target || card?.querySelector("summary"))?.focus({
        preventScroll: true,
      });
    }
  }
  async function refresh() {
    const results = await Promise.allSettled([usage.refresh(), refreshRuns()]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) throw failed.reason;
  }
  action($("execution-refresh"), refresh);
  return {
    refresh,
    clear() {
      epoch++;
      usage.clear();
      budgets.clear();
      records.clear();
      $("execution-content").replaceChildren();
    },
  };
}
