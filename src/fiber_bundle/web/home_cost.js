import { joinText, phrase, setText, uiText } from "./i18n/index.js";
// Thirty-day estimated cloud cost: one series, one axis, hover detail and a table view.
import { el } from "./ui.js";
import { money, costText } from "./usage_chart.js";
import { card, stampText } from "./ui_cards.js";

const ns = "http://www.w3.org/2000/svg";
let tableView = false;
let metric = "tokens";
// Headline amounts to the cent; non-zero sub-cent totals are not shown as $0.00.
function usd(nusd) {
  if (!Number.isSafeInteger(nusd) || nusd < 0) return "—";
  if (nusd > 0 && nusd < 1e7) return "< $0.01";
  return (
    "$" +
    (nusd / 1e9).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}
function svgNode(tag, attributes = {}) {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attributes))
    node.setAttribute(key, value);
  return node;
}
function niceMax(value) {
  if (value <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 2, 2.5, 5, 10])
    if (factor * step >= value) return factor * step;
  return 10 * step;
}
function tokensText(day) {
  if (
    !day ||
    !Number.isSafeInteger(day.input_tokens) ||
    !Number.isSafeInteger(day.output_tokens)
  )
    return uiText("尚未读取");
  if (day.calls > 0 && day.token_unknown_calls === day.calls)
    return uiText("未报告");
  return (
    (day.input_tokens + day.output_tokens).toLocaleString("en-US") +
    (day.token_unknown_calls ? " · " + uiText("部分用量未报告") : "")
  );
}
function chart(days) {
  const width = 360,
    height = 170,
    left = 40,
    bottom = 22,
    top = 8,
    plot = height - bottom - top;
  const amount = (day) =>
    metric === "tokens"
      ? Number.isSafeInteger(day.input_tokens) &&
        Number.isSafeInteger(day.output_tokens)
        ? day.input_tokens + day.output_tokens
        : 0
      : day.estimated_nusd / 1e9;
  const peak = niceMax(Math.max(...days.map(amount)));
  const axis = (value) =>
    metric === "tokens"
      ? new Intl.NumberFormat("en-US", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(value)
      : money(Math.round(value * 1e9));
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "cost-chart",
    role: "img",
    "aria-label": phrase(
      "近 30 天每日{p0}柱状图，峰值刻度 {p1}；表格视图提供逐日数值。",
      {
        p0: metric === "tokens" ? "Tokens" : uiText("估算费用"),
        p1: axis(peak),
      },
    ).toString(),
  });
  for (const fraction of [0, 0.5, 1]) {
    const y = top + plot * (1 - fraction);
    svg.append(
      svgNode("line", {
        x1: left,
        x2: width,
        y1: y,
        y2: y,
        class: fraction ? "grid" : "baseline",
      }),
      Object.assign(
        svgNode("text", {
          x: left - 6,
          y: y + 4,
          "text-anchor": "end",
          class: "axis",
        }),
        {
          textContent: axis(peak * fraction),
        },
      ),
    );
  }
  const slot = (width - left) / days.length,
    bar = Math.max(2, slot - 2);
  const tip = el("p", null, "cost-tip");
  tip.hidden = true;
  days.forEach((day, index) => {
    const value = amount(day),
      h = value > 0 ? Math.max(2, (plot * value) / peak) : 0,
      x = left + index * slot + (slot - bar) / 2,
      y = top + plot - h,
      r = Math.min(2, h / 2, bar / 2);
    const group = svgNode("g", {
      class: "bar",
      tabindex: "0",
      role: "img",
      "aria-label": `${day.day} · ${day.available ? tokensText(day) + " Tokens · " + uiText(costText(day)) : uiText("尚未读取")}`,
    });
    group.append(
      svgNode("rect", {
        x: left + index * slot,
        y: top,
        width: slot,
        height: plot,
        class: "hit",
      }),
    );
    if (h > 0)
      group.append(
        svgNode("path", {
          d: `M${x},${top + plot}V${y + r}Q${x},${y} ${x + r},${y}H${x + bar - r}Q${x + bar},${y} ${x + bar},${y + r}V${top + plot}Z`,
          class: "value",
        }),
      );
    const show = () => {
      setText(
        tip,
        joinText([
          "",
          day.day,
          " · ",
          day.available
            ? joinText([
                tokensText(day),
                " Tokens · ",
                uiText(costText(day)),
                " · ",
                phrase("调用 {p0} 次", { p0: day.calls }),
              ])
            : uiText("尚未读取"),
          "",
        ]),
      );
      tip.hidden = false;
    };
    group.addEventListener("pointerenter", show);
    group.addEventListener("focus", show);
    group.addEventListener("blur", () => (tip.hidden = true));
    group.addEventListener("pointerleave", () => (tip.hidden = true));
    svg.append(group);
    const last = index === days.length - 1;
    if ((days.length - 1 - index) % 7 === 0)
      svg.append(
        Object.assign(
          svgNode("text", {
            x: last ? width : left + index * slot + slot / 2,
            y: height - 6,
            "text-anchor": last ? "end" : "middle",
            class: "axis",
          }),
          { textContent: day.day.slice(5).replace("-", "/") },
        ),
      );
  });
  const figure = el("figure", null, "cost-figure");
  figure.append(svg, tip);
  return figure;
}
function table(days) {
  const node = el("table", null, "cost-table"),
    thead = el("thead"),
    head = el("tr");
  for (const text of ["日期", "Tokens", "估算费用", "预留", "调用记录"]) {
    const cell = el("th", uiText(text));
    cell.scope = "col";
    head.append(cell);
  }
  const body = el("tbody");
  for (const day of [...days].reverse()) {
    const row = el("tr");
    row.append(
      el("td", day.day),
      el("td", day.available ? tokensText(day) : "—"),
      el("td", day.available ? uiText(costText(day)) : uiText("尚未读取")),
      el("td", day.available ? money(day.reserved_nusd) : "—"),
      el("td", day.available ? String(day.calls) : "—"),
    );
    body.append(row);
  }
  thead.append(head);
  node.append(
    el("caption", uiText("近 30 天每日估算费用（UTC）")),
    thead,
    body,
  );
  const wrap = el("div", null, "cost-table-wrap");
  wrap.append(node);
  return wrap;
}
export function costCard(summary, record, rerender) {
  const { node, head } = card("模型用量", "home-cost-title");
  node.classList.add("cost-card");
  const toggle = el("button", uiText("表格"), "ui-small");
  toggle.type = "button";
  toggle.setAttribute("aria-pressed", String(tableView));
  toggle.addEventListener("click", () => {
    tableView = !tableView;
    rerender();
  });
  const detail = el("button", uiText("明细"), "quiet ui-link");
  detail.type = "button";
  detail.dataset.pageTarget = "execution";
  detail.dataset.showUsage = "true";
  const tools = el("div", null, "ui-card-actions");
  tools.append(toggle, detail);
  head.append(tools);
  if (!summary) {
    node.append(
      el(
        "p",
        uiText(stampText(record)),
        record?.error ? "ui-empty error" : "ui-empty",
      ),
    );
    return node;
  }
  const hero = el("dl", null, "cost-totals");
  const lifetime = summary.lifetime;
  for (const [label, value] of [
    ["累计已记录 Tokens", lifetime ? tokensText(lifetime) : uiText("尚未读取")],
    [
      "累计估算费用",
      !lifetime ? uiText("尚未读取") : uiText(costText(lifetime)),
    ],
  ]) {
    const item = el("div");
    item.append(el("dt", uiText(label)), el("dd", uiText(value)));
    hero.append(item);
  }
  if (lifetime)
    hero.append(
      el(
        "div",
        phrase("输入 {p0} · 输出 {p1}", {
          p0: lifetime.input_tokens.toLocaleString("en-US"),
          p1: lifetime.output_tokens.toLocaleString("en-US"),
        }),
        "cost-token-split",
      ),
    );
  const facts = el("dl", null, "cost-facts");
  for (const [term, value] of [
    [
      "本月至今",
      summary.monthAllUnknown
        ? "未知"
        : usd(summary.monthToDate) +
          (summary.monthUnknown ? "（部分未知）" : ""),
    ],
    ["预留中", usd(summary.reserved)],
    [
      "调用",
      summary.calls === null
        ? "未完整读取"
        : phrase("{p0} 次", { p0: summary.calls.toLocaleString("en-US") }),
    ],
  ]) {
    const item = el("div");
    item.append(el("dt", uiText(term)), el("dd", uiText(value)));
    facts.append(item);
  }
  const switcher = el("div", null, "cost-metric");
  switcher.setAttribute("role", "group");
  switcher.setAttribute("aria-label", uiText("近 30 天用量指标"));
  for (const [key, label] of [
    ["tokens", "Tokens"],
    ["cost", "费用"],
  ]) {
    const button = el("button", uiText(label), "ui-small");
    button.type = "button";
    button.setAttribute("aria-pressed", String(metric === key));
    button.addEventListener("click", () => {
      metric = key;
      rerender();
    });
    switcher.append(button);
  }
  node.append(
    hero,
    facts,
    el("h3", uiText("近 30 天消耗"), "cost-period"),
    switcher,
    tableView
      ? table(summary.days)
      : metric === "cost" && summary.allUnknown
        ? el("p", uiText("费用尚未确认，已保留调用记录。"), "ui-empty")
        : chart(summary.days),
  );
  node.append(
    el(
      "p",
      uiText(
        "累计值来自全部已记录云调用；图表按 UTC 展示近 30 天，费用按配置单价估算。",
      ),
      "ui-note",
    ),
  );
  if (summary.tokenUnknownCalls || lifetime?.token_unknown_calls)
    node.append(
      el("p", uiText("未报告的用量不计入统计，不能视作零消耗。"), "ui-note"),
    );
  if (record?.error)
    node.append(el("p", uiText(stampText(record)), "ui-stamp"));
  return node;
}
