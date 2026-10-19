import { joinText, phrase, setText, uiText } from "./i18n/index.js";
// Date/key filters, totals and accessible tables share a single server-side projection.
import { $, el, message, setBusy } from "./ui.js";
import { money, costText, renderUsageChart } from "./usage_chart.js";

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
function cell(row, text, header = false, numeric = false) {
  const node = el(header ? "th" : "td", header ? uiText(text) : text);
  if (header) node.scope = "col";
  if (numeric) {
    node.className = "usage-numeric";
    if (!header) node.dataset.uiNumber = "";
  }
  row.append(node);
  return node;
}
function renderTable(root, headers, rows, caption, numericFrom = 2) {
  const table = el("table"),
    head = el("thead"),
    body = el("tbody"),
    titles = el("tr");
  table.append(el("caption", uiText(caption)));
  headers.forEach((title, index) =>
    cell(titles, title, true, index >= numericFrom),
  );
  head.append(titles);
  for (const values of rows) {
    const row = el("tr");
    values.forEach((value, index) =>
      cell(row, value, false, index >= numericFrom),
    );
    body.append(row);
  }
  table.append(head, body);
  root.replaceChildren(table);
}
export function createUsageView(api) {
  let epoch = 0,
    disabled = null;
  const form = $("usage-filters"),
    period = $("usage-period"),
    key = $("usage-key");
  const start = $("usage-start"),
    end = $("usage-end");
  function chooseDates() {
    const custom = period.value === "custom";
    start.disabled = end.disabled = !custom;
    if (!custom) {
      const dates = periodDates(period.value);
      start.value = dates.start;
      end.value = dates.end;
    }
  }
  chooseDates();
  period.addEventListener("change", chooseDates);
  function unlock() {
    if (!disabled) return;
    for (const [field, previous] of disabled) field.disabled = previous;
    disabled = null;
  }
  async function refresh() {
    if (period.value !== "custom") chooseDates();
    if (!form.reportValidity()) return;
    const current = ++epoch,
      button = $("usage-apply");
    if (!disabled)
      disabled = [...form.querySelectorAll("input,select")].map((field) => [
        field,
        field.disabled,
      ]);
    for (const [field] of disabled) field.disabled = true;
    button.disabled = true;
    setBusy(button, true);
    setText($("usage-state"), uiText("正在更新用量…"));
    try {
      const data = await api.usageReport({
        start: start.value,
        end: end.value,
        api_key_id: key.value || "all",
      });
      if (current !== epoch) return;
      const selected = key.value;
      const all = el("option", uiText("全部 API Key"));
      all.value = "all";
      key.replaceChildren(all);
      for (const item of data.keys) {
        const option = el(
          "option",
          joinText([item.label, item.configured ? uiText(" · 当前配置") : ""]),
        );
        option.value = item.id;
        key.append(option);
      }
      key.value = [...key.options].some((option) => option.value === selected)
        ? selected
        : "all";
      setText(
        $("usage-local-calls"),
        Number.isSafeInteger(data.local_calls)
          ? uiText(String(data.local_calls))
          : uiText("未记录"),
      );
      const totals = data.summary;
      setText($("usage-total-cost"), uiText(costText(totals)));
      setText(
        $("usage-total-calls"),
        uiText(totals.calls.toLocaleString("zh-CN")),
      );
      setText(
        $("usage-total-tokens"),
        uiText(
          (totals.input_tokens + totals.output_tokens).toLocaleString("zh-CN"),
        ),
      );
      setText($("usage-total-pending"), uiText(money(totals.reserved_nusd)));
      setText(
        $("usage-pending-count"),
        totals.price_unknown_calls === undefined
          ? phrase("{p0} 条费用待确认", { p0: totals.unsettled_calls })
          : phrase("缺少价格 {p0} 条 · 处理中 {p1} 条 · 结果未知 {p2} 条", {
              p0: totals.price_unknown_calls,
              p1: totals.pending_calls,
              p2: totals.result_unknown_calls,
            }),
      );
      renderUsageChart($("usage-chart"), data.daily, $("usage-day-detail"));
      renderTable(
        $("usage-keys-table"),
        [
          "API Key / 凭据配置",
          "模型",
          "调用记录",
          "Tokens",
          "估算费用 · USD",
          "待确认预留 · USD",
        ],
        data.by_key.map((item) => [
          joinText([item.label, item.configured ? uiText(" · 当前配置") : ""]),
          item.models.join(" / ") || "—",
          item.calls.toLocaleString("zh-CN"),
          (item.input_tokens + item.output_tokens).toLocaleString("zh-CN"),
          uiText(costText(item)),
          money(item.reserved_nusd),
        ]),
        "按 API Key 汇总",
      );
      renderTable(
        $("usage-daily-table"),
        [
          "日期 · UTC",
          "调用记录",
          "已结算记录",
          "估算费用 · USD",
          "待确认预留 · USD",
        ],
        data.daily.map((day) => [
          day.day,
          String(day.calls),
          String(day.settled_calls),
          uiText(costText(day)),
          money(day.reserved_nusd),
        ]),
        "每日用量与费用",
        1,
      );
      setText(
        $("usage-state"),
        phrase("{p0} — {p1} · UTC · 已更新", { p0: data.start, p1: data.end }),
      );
    } catch (error) {
      if (current === epoch) {
        setText(
          $("usage-state"),
          uiText("用量更新失败；此前数据如有保留，仅供核对。"),
        );
        message(
          error.status === 422
            ? "请核对日期范围（最长 366 天）和 API Key，再更新用量。"
            : error.message,
          true,
          "execution",
        );
      }
      throw error;
    } finally {
      if (current === epoch) {
        unlock();
        button.disabled = false;
        setBusy(button, false);
      }
    }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    refresh().catch(() => {});
  });
  return {
    refresh,
    clear() {
      epoch++;
      unlock();
      for (const id of ["usage-chart", "usage-keys-table", "usage-daily-table"])
        $(id).replaceChildren();
      for (const id of [
        "usage-local-calls",
        "usage-total-cost",
        "usage-total-calls",
        "usage-total-tokens",
        "usage-total-pending",
      ])
        setText($(id), uiText("—"));
      setText($("usage-state"), uiText(""));
      setText($("usage-day-detail"), uiText(""));
      setText($("usage-pending-count"), uiText(""));
      const all = el("option", uiText("全部 API Key"));
      all.value = "all";
      key.replaceChildren(all);
      key.value = "all";
      $("usage-apply").disabled = false;
      setBusy($("usage-apply"), false);
    },
  };
}
