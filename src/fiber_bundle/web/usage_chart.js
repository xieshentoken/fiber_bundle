import { setText, uiText } from "./i18n/index.js";
// SVG scales use daily ledger totals, with separate axes for counts and estimated USD.
export function money(nusd) {
  if (!Number.isSafeInteger(nusd) || nusd < 0) return "—";
  const usd = nusd / 1e9;
  return (
    "$" +
    usd.toLocaleString("en-US", {
      minimumFractionDigits: usd === 0 ? 2 : usd >= 0.01 ? 2 : 0,
      maximumFractionDigits: usd >= 0.01 ? 6 : 9,
    })
  );
}
export function costText(item) {
  return item.calls > 0 && item.settled_calls === 0
    ? "未知"
    : money(item.estimated_nusd) +
        (item.unsettled_calls > 0 ? "（部分未知）" : "");
}
export function chartGeometry(days) {
  const width = 960,
    left = 110,
    right = 24,
    top = 28,
    panelHeight = 110,
    gap = 52,
    bottom = 36;
  const callTop = top + panelHeight + gap,
    height = callTop + panelHeight + bottom;
  const plotWidth = width - left - right,
    plotHeight = panelHeight;
  const callMax = Math.max(
    4,
    Math.ceil(Math.max(0, ...days.map((day) => day.calls)) / 4) * 4,
  );
  const cost =
    Math.max(0, ...days.map((day) => day.estimated_nusd)) || 10000000;
  const magnitude = 10 ** Math.floor(Math.log10(cost));
  const costMax = Math.max(2, Math.ceil(cost / magnitude) * magnitude);
  const step = plotWidth / Math.max(1, days.length);
  return {
    width,
    height,
    left,
    top,
    callTop,
    right,
    bottom,
    plotWidth,
    plotHeight,
    callMax,
    costMax,
    step,
    points: days.map((day, index) => ({
      ...day,
      x: left + step * (index + 0.5),
      costY: top + plotHeight * (1 - day.estimated_nusd / costMax),
      callY: callTop + plotHeight * (1 - day.calls / callMax),
    })),
  };
}
function svgNode(tag, attrs = {}, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attrs))
    node.setAttribute(name, String(value));
  if (text !== undefined) setText(node, uiText(text));
  return node;
}
export function renderUsageChart(root, days, detail) {
  root.replaceChildren();
  setText(
    detail,
    uiText(
      "上图为已结算的估算费用，下图为调用记录，刻度各自独立。未知费用保留缺口，可聚焦日期核对。",
    ),
  );
  if (!days.some((day) => day.calls)) {
    const empty = document.createElement("div");
    empty.className = "usage-empty";
    const title = document.createElement("h3");
    setText(title, uiText("这段时间暂无调用记录"));
    const text = document.createElement("p");
    setText(text, uiText("更换日期或 API Key 查看其他记录。"));
    empty.append(title, text);
    root.append(empty);
    return;
  }
  const graph = chartGeometry(days),
    { left, top, callTop, width, height, plotWidth, plotHeight } = graph;
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "group",
    "aria-label": "每日估算费用与调用记录，上下两图共用日期轴、各自独立刻度",
  });
  svg.append(svgNode("title", {}, "估算费用折线图与调用记录柱状图"));
  svg.append(
    svgNode(
      "desc",
      {},
      "预留金额不计入费用折线。使用 Tab 键或指针查看每日数据，下方提供表格。",
    ),
  );
  for (const [y0, max, title, format] of [
    [
      top,
      graph.costMax,
      "估算费用（USD，已结算）",
      (v) => money(Math.round(v)),
    ],
    [callTop, graph.callMax, "调用记录（次）", (v) => String(v)],
  ]) {
    svg.append(
      svgNode("text", { x: left, y: y0 - 12, class: "chart-title" }, title),
    );
    for (let index = 0; index <= 2; index++) {
      const y = y0 + plotHeight * (1 - index / 2);
      svg.append(
        svgNode("line", {
          x1: left,
          x2: left + plotWidth,
          y1: y,
          y2: y,
          class: "chart-grid",
        }),
        svgNode(
          "text",
          {
            x: left - 10,
            y: y + 4,
            "text-anchor": "end",
            class: "chart-label",
          },
          format((max * index) / 2),
        ),
      );
    }
  }
  const barWidth = Math.min(24, graph.step * 0.6),
    labelEvery = Math.max(1, Math.ceil(days.length / 7));
  for (const [index, point] of graph.points.entries()) {
    svg.append(
      svgNode("rect", {
        x: point.x - barWidth / 2,
        y: point.callY,
        width: barWidth,
        height: callTop + plotHeight - point.callY,
        rx: 2,
        class: "chart-bar",
      }),
    );
    if (index % labelEvery === 0 || index === days.length - 1)
      svg.append(
        svgNode(
          "text",
          {
            x: point.x,
            y: height - 12,
            "text-anchor": "middle",
            class: "chart-label",
          },
          point.day.slice(5).replace("-", "/"),
        ),
      );
  }
  let segment = [];
  function appendSegment() {
    if (segment.length)
      svg.append(
        svgNode("polyline", { points: segment.join(" "), class: "chart-cost" }),
      );
    segment = [];
  }
  for (const point of graph.points) {
    if (point.calls > 0 && point.settled_calls === 0) appendSegment();
    else segment.push(`${point.x},${point.costY}`);
  }
  appendSegment();
  const targets = [];
  for (const [index, point] of graph.points.entries()) {
    const label = `${point.day} · ${point.calls} 次 · 估算 ${costText(point)} · 待确认 ${money(point.reserved_nusd)}`;
    const group = svgNode("g", {
      tabindex: index === 0 ? "0" : "-1",
      role: "img",
      "aria-label": label,
      class: "chart-day",
    });
    group.append(svgNode("title", {}, label));
    if (!(point.calls > 0 && point.settled_calls === 0))
      group.append(
        svgNode("circle", {
          cx: point.x,
          cy: point.costY,
          r: days.length <= 31 ? 3 : 1.5,
          class: "chart-dot",
        }),
      );
    group.append(
      svgNode("rect", {
        x: point.x - graph.step / 2,
        y: top,
        width: graph.step,
        height: callTop + plotHeight - top,
        class: "chart-hit",
      }),
    );
    for (const name of ["pointerenter", "focus", "click"])
      group.addEventListener(name, () => {
        setText(detail, uiText(label));
      });
    group.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
        return;
      event.preventDefault();
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? targets.length - 1
            : Math.max(
                0,
                Math.min(
                  targets.length - 1,
                  index + (event.key === "ArrowRight" ? 1 : -1),
                ),
              );
      for (const [position, node] of targets.entries())
        node.setAttribute("tabindex", position === next ? "0" : "-1");
      targets[next].focus();
    });
    targets.push(group);
    svg.append(group);
  }
  root.append(svg);
}
