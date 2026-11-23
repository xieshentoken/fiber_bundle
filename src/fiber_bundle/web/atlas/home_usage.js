// Home-only presentation and display preferences; no writes to the usage ledger.
import { h } from "./dom.js";
import { strings } from "./i18n.js";
import { card, empty, segmented } from "./ui.js";
import { chartWithTable } from "./chart.js";
import { number, usd } from "./format.js";
import { knownCost, ledgerCost } from "./usage_model.js";

const t = strings({
  zh: {
    title: "模型用量",
    tokens: "Tokens",
    cost: "估算费用",
    calls: "调用",
    reserved: "预留",
    lifetimeTokens: "累计已记录 Tokens",
    lifetimeCost: "累计估算费用",
    period:
      "{start} – {end}（UTC）· {calls} 次记录调用 · 预留 {reserved} · 本地调用 {local} 次（不计费）",
    note: "累计值来自全部已记录云调用；每日统计按 UTC 展示近 30 天。费用按配置单价估算。",
    unread: "用量尚未读取",
    failed: "用量读取失败；请刷新重试。",
    metric: "用量指标",
    unreported: "未报告",
    partial: "部分用量未报告",
    reason: "费用说明",
    price: "{n} 次调用缺少价格记录",
    pending: "{n} 次调用尚未完成",
    result: "{n} 次调用结果未确认",
    unknown: "费用记录尚未完整确认",
    tokenNote: "未报告的用量不能视作零消耗。",
  },
  en: {
    title: "Model usage",
    tokens: "Tokens",
    cost: "Estimated cost",
    calls: "Calls",
    reserved: "Reserved",
    lifetimeTokens: "All-time recorded tokens",
    lifetimeCost: "All-time estimated cost",
    period:
      "{start} – {end} (UTC) · {calls} recorded calls · {reserved} reserved · {local} local calls (not billed)",
    note: "All-time values include all recorded cloud calls; daily statistics cover 30 UTC days. Cost is estimated from configured prices.",
    unread: "Usage not read yet",
    failed: "Usage could not be read; refresh to retry.",
    metric: "Usage metric",
    unreported: "Not reported",
    partial: "Some usage not reported",
    reason: "Cost details",
    price: "{n} calls have no recorded price",
    pending: "{n} calls are pending",
    result: "{n} call results are unconfirmed",
    unknown: "Cost records are incomplete",
    tokenNote: "Unreported usage must not be treated as zero consumption.",
  },
});

export function knownTokens(row) {
  if (
    row?.available === false ||
    !Number.isSafeInteger(row?.input_tokens) ||
    !Number.isSafeInteger(row?.output_tokens) ||
    row.input_tokens < 0 ||
    row.output_tokens < 0 ||
    (row.calls > 0 && row.token_unknown_calls === row.calls)
  )
    return null;
  const value = row.input_tokens + row.output_tokens;
  return Number.isSafeInteger(value) ? value : null;
}
export function tokensText(row) {
  const value = knownTokens(row);
  if (value === null) return row?.available === false ? "—" : t("unreported");
  return number(value) + (row.token_unknown_calls ? " · " + t("partial") : "");
}
export function costReasons(row) {
  if (row?.available === false) return t("unread");
  const reasons = [
    ["price_unknown_calls", "price"],
    ["pending_calls", "pending"],
    ["result_unknown_calls", "result"],
  ]
    .filter(([key]) => Number.isSafeInteger(row?.[key]) && row[key] > 0)
    .map(([key, label]) => t(label, { n: number(row[key]) }));
  return reasons.join(" · ") || (row?.unsettled_calls > 0 ? t("unknown") : "");
}
const preferenceKey = "fiber.atlas.home.usage-display.v1";
export function createUsageState(storage) {
  // Session display preferences contain no usage data and can safely fail closed.
  if (storage === undefined) {
    try {
      storage = globalThis.sessionStorage;
    } catch {
      storage = null;
    }
  }
  let saved;
  try {
    saved = JSON.parse(storage?.getItem(preferenceKey) || "null");
  } catch {
    saved = null;
  }
  const state = {
    metric: saved?.metric === "cost" ? "cost" : "tokens",
    mode: saved?.mode === "table" ? "table" : "chart",
  };
  state.save = () => {
    try {
      storage?.setItem(
        preferenceKey,
        JSON.stringify({ metric: state.metric, mode: state.mode }),
      );
    } catch {
      /* Optional display storage. */
    }
  };
  return state;
}

export function homeUsageCard(cost, record, state) {
  if (!cost)
    return card(
      { title: t("title") },
      empty(record?.error ? t("failed") : t("unread")),
    );
  const node = h("div");
  node.style.minWidth = "0";
  const draw = () => {
    const isTokens = state.metric === "tokens";
    const title = t(isTokens ? "tokens" : "cost");
    const rows = cost.days.map((row) => ({
      ...row,
      value: isTokens ? knownTokens(row) : knownCost(row),
    }));
    const totals = cost.days.reduce((sum, row) => {
      for (const key of [
        "price_unknown_calls",
        "pending_calls",
        "result_unknown_calls",
        "unsettled_calls",
      ])
        sum[key] = (sum[key] || 0) + (row[key] || 0);
      return sum;
    }, {});
    const reasons = costReasons(totals);
    node.replaceChildren(
      card(
        {
          title: t("title"),
          subtitle: t("period", {
            start: cost.start,
            end: cost.end,
            calls: cost.calls === null ? "—" : number(cost.calls),
            reserved: usd(cost.reserved),
            local: number(cost.localCalls),
          }),
        },
        h(
          "div.a-grid-2",
          h(
            "div",
            h("p.a-subtle", t("lifetimeTokens")),
            h("strong", cost.lifetime ? tokensText(cost.lifetime) : "—"),
          ),
          h(
            "div",
            h("p.a-subtle", t("lifetimeCost")),
            h("strong", cost.lifetime ? ledgerCost(cost.lifetime) : "—"),
          ),
        ),
        reasons ? h("p.a-subtle", t("reason") + "：" + reasons) : null,
        cost.tokenUnknownCalls || cost.lifetime?.token_unknown_calls
          ? h("p.a-subtle", t("tokenNote"))
          : null,
        segmented(
          [
            { value: "tokens", label: t("tokens") },
            { value: "cost", label: t("cost") },
          ],
          state.metric,
          (value) => {
            state.metric = value;
            state.save();
            draw();
          },
          t("metric"),
        ),
        chartWithTable({
          title,
          rows,
          viewState: state,
          onModeChange: () => state.save(),
          format: (value, row) =>
            row
              ? isTokens
                ? tokensText(row)
                : ledgerCost(row)
              : isTokens
                ? new Intl.NumberFormat("en-US", {
                    notation: "compact",
                    maximumFractionDigits: 1,
                  }).format(value)
                : usd(Math.round(value)),
          scale: isTokens ? 1 : 1e9,
          extra: (row) =>
            `${t("calls")} ${number(row.calls)}${costReasons(row) ? " · " + costReasons(row) : ""}`,
          columns: [
            {
              label: t(isTokens ? "cost" : "tokens"),
              value: (row) => (isTokens ? ledgerCost(row) : tokensText(row)),
              className: "is-num",
            },
            {
              label: t("reserved"),
              value: (row) =>
                row.available === false ? "—" : usd(row.reserved_nusd),
              className: "is-num",
            },
            {
              label: t("calls"),
              value: (row) =>
                row.available === false ? "—" : number(row.calls),
              className: "is-num",
            },
            { label: t("reason"), value: costReasons },
          ],
        }),
        h("p.a-subtle", t("note")),
      ),
    );
  };
  draw();
  return node;
}
