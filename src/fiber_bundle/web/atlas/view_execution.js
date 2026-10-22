// Runs & cost. Cloud cost is an estimate from configured prices; local calls are counted
// separately. Unknown outcomes are reconciled, never replayed. Calls and cost are two
// separate single-axis charts.
import { ledgerCost, knownCost } from "./usage_model.js";
import { h, clear } from "./dom.js";
import { strings } from "./i18n.js";
import {
  badge,
  button,
  card,
  chips,
  codeBlock,
  disclosure,
  empty,
  failure,
  field,
  input,
  select,
  setOptions,
  skeleton,
  stat,
  table,
  tabs,
} from "./ui.js";
import { run, toast } from "./feedback.js";
import { confirm } from "./dialog.js";
import { chartWithTable } from "./chart.js";
import { bindingLabel, callState, runState } from "./labels.js";
import { number, shortDate, shortId, usd } from "./format.js";

const t = strings({
  zh: {
    usage: "用量与费用",
    runs: "执行记录",
    period: "时间范围",
    p7: "近 7 天",
    p30: "近 30 天",
    month: "本月",
    custom: "自定义",
    start: "开始（UTC）",
    end: "结束（UTC）",
    key: "API Key",
    allKeys: "全部 API Key",
    current: "当前配置",
    apply: "更新",
    badRange: "请核对日期范围（最长 366 天）和 API Key，再更新用量。",
    cost: "估算费用",
    calls: "调用记录",
    tokens: "Tokens",
    reserved: "待确认预留",
    unsettled: "{n} 条待结算或结果未知",
    local: "本地调用",
    localHint: "不计费",
    notRecorded: "未记录",
    costChart: "每日估算费用（USD）",
    callsChart: "每日调用记录",
    byKey: "按 API Key 汇总",
    keyCol: "API Key / 凭据配置",
    models: "模型",
    range: "{start} — {end} · UTC",
    basis: "按配置单价估算；不代表服务商账单。",
    none: "暂无执行任务或模型费用。",
    limit: "任务上限 {v} · 尝试 {n}",
    uncertain: "未结算或未知请求：{n}；预留与原记录保留。",
    callsTable: "模型请求与费用明细（{n}）",
    noCalls: "尚无模型请求记录。",
    model: "模型",
    state: "状态",
    charged: "已计费",
    day: "计费日",
    unknown: "未记录",
    tech: "技术详情",
    cancel: "取消当前执行",
    cancelTitle: "取消此执行？",
    cancelBody: "原件、已知费用和未知结果会保留。",
    keep: "保留执行",
    reconcile: "对账中断执行",
    reconcileTitle: "对账中断执行？",
    reconcileBody: "仅对账已失去负责人的执行，未知请求不会重放。",
    reconcileConfirm: "开始对账",
    task: "任务 {id}",
    settled: "已结算",
  },
  en: {
    usage: "Usage & cost",
    runs: "Runs",
    period: "Period",
    p7: "Last 7 days",
    p30: "Last 30 days",
    month: "This month",
    custom: "Custom",
    start: "Start (UTC)",
    end: "End (UTC)",
    key: "API key",
    allKeys: "All API keys",
    current: "current",
    apply: "Update",
    badRange: "Check the date range (max 366 days) and API key, then update.",
    cost: "Estimated cost",
    calls: "Calls recorded",
    tokens: "Tokens",
    reserved: "Pending reservations",
    unsettled: "{n} unsettled or unknown",
    local: "Local calls",
    localHint: "not billed",
    notRecorded: "not recorded",
    costChart: "Estimated daily cost (USD)",
    callsChart: "Daily calls recorded",
    byKey: "By API key",
    keyCol: "API key / credential",
    models: "Models",
    range: "{start} — {end} · UTC",
    basis: "Estimated from configured prices; not a provider invoice.",
    none: "No runs or model costs yet.",
    limit: "Task limit {v} · {n} attempts",
    uncertain:
      "Unsettled or unknown requests: {n}; reservations and records are kept.",
    callsTable: "Model requests and cost ({n})",
    noCalls: "No model requests recorded.",
    model: "Model",
    state: "State",
    charged: "Charged",
    day: "Billing day",
    unknown: "not recorded",
    tech: "Technical details",
    cancel: "Cancel run",
    cancelTitle: "Cancel this run?",
    cancelBody: "Originals, known costs and unknown outcomes are kept.",
    keep: "Keep run",
    reconcile: "Reconcile interrupted run",
    reconcileTitle: "Reconcile this interrupted run?",
    reconcileBody:
      "Only reconciles runs that lost their owner; unknown requests are never replayed.",
    reconcileConfirm: "Reconcile",
    task: "Task {id}",
    settled: "Settled",
  },
});
export function periodDates(period, now = new Date()) {
  const end = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );
  const start = new Date(end);
  if (period === "month") start.setUTCDate(1);
  else start.setUTCDate(start.getUTCDate() - (period === "7" ? 6 : 29));
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

export function createView(ctx, route) {
  let tab = route.sub === "runs" ? "runs" : "usage",
    period = "30",
    epoch = 0;
  const tabHost = h("div");
  const body = h("div.a-stack");
  const startInput = input({
    type: "date",
    required: true,
    dataset: { draft: "ignore" },
  });
  const endInput = input({
    type: "date",
    required: true,
    dataset: { draft: "ignore" },
  });
  const keySelect = select([["all", t("allKeys")]], "all", {
    dataset: { draft: "ignore" },
  });
  const periodHost = h("div");
  const usageHost = h("div.a-stack");
  function setDates() {
    const custom = period === "custom";
    startInput.disabled = endInput.disabled = !custom;
    if (!custom) {
      const dates = periodDates(period);
      startInput.value = dates.start;
      endInput.value = dates.end;
    }
  }
  function drawPeriods() {
    clear(
      periodHost,
      chips(
        ["7", "30", "month", "custom"].map((value) => ({
          value,
          label: t(value === "7" ? "p7" : value === "30" ? "p30" : value),
        })),
        period,
        (value) => {
          period = value;
          drawPeriods();
          setDates();
          if (value !== "custom") void run(applyButton, loadUsage);
        },
        t("period"),
      ),
    );
  }
  const applyButton = button(t("apply"), {
    variant: "primary",
    iconName: "refresh",
  });
  applyButton.addEventListener("click", () => run(applyButton, loadUsage));
  async function loadUsage() {
    if (period !== "custom") setDates();
    const mine = ++epoch;
    if (!usageHost.childElementCount) clear(usageHost, skeleton(5));
    let data;
    try {
      data = await ctx.api.usageReport({
        start: startInput.value,
        end: endInput.value,
        api_key_id: keySelect.value || "all",
      });
    } catch (error) {
      if (mine === epoch) {
        if (error.status === 422) toast(t("badRange"), { tone: "error" });
        clear(usageHost, failure(error, loadUsage));
      }
      return;
    }
    if (mine !== epoch) return;
    const selected = keySelect.value;
    setOptions(
      keySelect,
      [
        ["all", t("allKeys")],
        ...data.keys.map((k) => [
          k.id,
          k.label + (k.configured ? " · " + t("current") : ""),
        ]),
      ],
      data.keys.some((k) => k.id === selected) ? selected : "all",
    );
    const s = data.summary;
    const rows = data.daily.map((d) => ({ ...d, value: knownCost(d) }));
    clear(
      usageHost,
      h(
        "p.a-subtle",
        `${t("range", { start: data.start, end: data.end })} · ${t("basis")}`,
      ),
      h(
        "div.a-stats",
        stat(t("cost"), ledgerCost(s)),
        stat(t("calls"), number(s.calls)),
        stat(t("tokens"), number(s.input_tokens + s.output_tokens)),
        stat(t("reserved"), usd(s.reserved_nusd), {
          hint: t("unsettled", { n: s.unsettled_calls }),
          tone: s.unsettled_calls ? "waiting" : undefined,
        }),
        stat(
          t("local"),
          Number.isSafeInteger(data.local_calls)
            ? number(data.local_calls)
            : t("notRecorded"),
          { hint: t("localHint") },
        ),
      ),
      h(
        "div.a-grid-2",
        card(
          {},
          chartWithTable({
            title: t("costChart"),
            rows,
            format: (v, row) => (row ? ledgerCost(row) : usd(Math.round(v))),
            scale: 1e9,
            columns: [
              {
                label: t("reserved"),
                value: (r) => usd(r.reserved_nusd),
                className: "is-num",
              },
            ],
          }),
        ),
        card(
          {},
          chartWithTable({
            title: t("callsChart"),
            rows: data.daily.map((d) => ({ ...d, value: d.calls })),
            format: (v) => number(v),
            columns: [
              {
                label: t("settled"),
                value: (r) => number(r.settled_calls),
                className: "is-num",
              },
            ],
          }),
        ),
      ),
      card(
        { title: t("byKey") },
        table(
          [
            {
              label: t("keyCol"),
              value: (k) =>
                k.label + (k.configured ? " · " + t("current") : ""),
            },
            { label: t("models"), value: (k) => k.models.join(" / ") || "—" },
            {
              label: t("calls"),
              value: (k) => number(k.calls),
              className: "is-num",
            },
            {
              label: t("tokens"),
              value: (k) => number(k.input_tokens + k.output_tokens),
              className: "is-num",
            },
            {
              label: t("cost"),
              value: (k) => ledgerCost(k),
              className: "is-num",
            },
            {
              label: t("reserved"),
              value: (k) => usd(k.reserved_nusd),
              className: "is-num",
            },
          ],
          data.by_key,
          { empty: t("none") },
        ),
      ),
    );
  }
  function runCard(item) {
    const [stateText, tone] = runState(item.state);
    const uncertain = item.calls.filter((c) => c.state !== "settled").length;
    const actions = h("div.a-form-actions");
    if (
      ["queued", "running", "waiting_budget", "interrupted"].includes(
        item.state,
      )
    ) {
      const cancel = button(t("cancel"), {
        variant: "danger-outline",
        iconName: "stop",
      });
      cancel.addEventListener("click", () =>
        run(cancel, async () => {
          if (
            !(await confirm({
              title: t("cancelTitle"),
              body: t("cancelBody"),
              confirmLabel: t("cancel"),
              cancelLabel: t("keep"),
              tone: "danger",
            }))
          )
            return false;
          await ctx.api.cancelExecution(item.id);
          await loadRuns();
        }),
      );
      actions.append(cancel);
    }
    if (item.state === "running") {
      const reconcile = button(t("reconcile"), { iconName: "pulse" });
      reconcile.addEventListener("click", () =>
        run(reconcile, async () => {
          if (
            !(await confirm({
              title: t("reconcileTitle"),
              body: t("reconcileBody"),
              confirmLabel: t("reconcileConfirm"),
            }))
          )
            return false;
          await ctx.api.reconcileExecution(item.id);
          await loadRuns();
        }),
      );
      actions.append(reconcile);
    }
    return card(
      {
        title: `${bindingLabel(item.binding_kind)} · ${shortDate(item.updated_at || item.created_at)}`,
        subtitle: t("task", { id: shortId(item.id) }),
        actions: badge(stateText, tone),
      },
      h(
        "p.a-subtle",
        t("limit", { v: usd(item.task_limit_nusd), n: item.attempts }),
      ),
      item.error_code ? h("p.a-inline-error", item.error_code) : null,
      uncertain ? h("p.a-subtle", t("uncertain", { n: uncertain })) : null,
      disclosure(
        t("callsTable", { n: item.calls.length }),
        item.calls.length
          ? table(
              [
                { label: t("model"), value: (c) => c.model || t("unknown") },
                {
                  label: t("state"),
                  value: (c) => badge(...callState(c.state)),
                },
                {
                  label: t("reserved"),
                  value: (c) => usd(c.reserved_nusd),
                  className: "is-num",
                },
                {
                  label: t("charged"),
                  value: (c) =>
                    c.charged_nusd === null ? "—" : usd(c.charged_nusd),
                  className: "is-num",
                },
                { label: t("day"), value: (c) => c.billing_day || "—" },
              ],
              item.calls,
            )
          : h("p.a-subtle", t("noCalls")),
        disclosure(t("tech"), codeBlock(JSON.stringify(item, null, 2))),
      ),
      actions.childElementCount ? actions : null,
    );
  }
  async function loadRuns() {
    const mine = ++epoch;
    if (!usageHost.childElementCount) clear(usageHost, skeleton(4));
    const data = await ctx.api.inventoryExecutions();
    if (mine !== epoch) return;
    clear(
      usageHost,
      data.runs.length
        ? data.runs.map(runCard)
        : card({}, empty(t("none"), { iconName: "execution" })),
    );
  }
  function draw() {
    clear(
      tabHost,
      tabs(
        [
          { value: "usage", label: t("usage") },
          { value: "runs", label: t("runs") },
        ],
        tab,
        (value) => ctx.navigate("execution", value === "runs" ? "runs" : ""),
        t("usage"),
      ),
    );
    usageHost.replaceChildren();
    if (tab === "usage") {
      drawPeriods();
      setDates();
      clear(
        body,
        card(
          {},
          h(
            "div.a-usage-filters",
            h("div.a-field", h("label", t("period")), periodHost),
            field(t("start"), startInput),
            field(t("end"), endInput),
            field(t("key"), keySelect),
            applyButton,
          ),
        ),
        usageHost,
      );
    } else clear(body, usageHost);
  }
  draw();
  return {
    node: h("div.a-stack", tabHost, body),
    refresh: () => (tab === "usage" ? loadUsage() : loadRuns()),
    update(next) {
      tab = next.sub === "runs" ? "runs" : "usage";
      draw();
      void (tab === "usage" ? loadUsage() : loadRuns());
      return true;
    },
    dispose: () => epoch++,
  };
}
