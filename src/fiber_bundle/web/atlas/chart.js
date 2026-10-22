// Single-series daily bar chart: one y-axis, recessive grid, per-bar hover tooltip and a
// table alternative. Two measures are always two charts, never a dual axis.
import { h } from "./dom.js";
import { strings } from "./i18n.js";
import { segmented, table } from "./ui.js";

const t = strings({
  zh: {
    chart: "图表",
    table: "表格",
    view: "显示方式",
    day: "日期",
    peak: "{title}，{n} 天，刻度上限 {max}；表格视图提供逐日数值。",
  },
  en: {
    chart: "Chart",
    table: "Table",
    view: "View",
    day: "Day",
    peak: "{title}, {n} days, axis maximum {max}; the table view lists each day.",
  },
});
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes))
    node.setAttribute(key, value);
  return node;
}
export function niceMax(value) {
  if (!(value > 0)) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 2, 2.5, 5, 10])
    if (factor * step >= value) return factor * step;
  return 10 * step;
}
// rows: [{ day: "YYYY-MM-DD", value: number }]; format(value) → label text.
export function barChart({ title, rows, format, scale = 1, extra }) {
  const width = 640,
    height = 180,
    left = 52,
    right = 6,
    top = 10,
    bottom = 24,
    plot = height - top - bottom;
  const peak = niceMax(Math.max(0, ...rows.map((row) => row.value / scale)));
  const node = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "a-chart",
    role: "img",
    "aria-label": t("peak", {
      title,
      n: rows.length,
      max: format(peak * scale),
    }),
    preserveAspectRatio: "none",
  });
  for (const fraction of [0, 0.5, 1]) {
    const y = top + plot * (1 - fraction);
    node.append(
      svg("line", {
        x1: left,
        x2: width - right,
        y1: y,
        y2: y,
        class: fraction ? "a-chart-grid" : "a-chart-base",
      }),
    );
    const label = svg("text", {
      x: left - 8,
      y: y + 4,
      "text-anchor": "end",
      class: "a-chart-axis",
    });
    label.textContent = format(peak * fraction * scale);
    node.append(label);
  }
  const slot = (width - left - right) / Math.max(rows.length, 1);
  const bar = Math.max(2, slot - 2);
  const tip = h("div.a-chart-tip", { hidden: true, role: "status" });
  rows.forEach((row, index) => {
    const value = row.value / scale;
    if (!Number.isFinite(row.value)) {
      const missing = svg("text", {
        x: left + (index + 0.5) * slot,
        y: top + plot - 5,
        "text-anchor": "middle",
        class: "a-chart-axis",
      });
      missing.textContent = "?";
      node.append(missing);
    }
    const tall = peak ? (value / peak) * plot : 0;
    const x = left + index * slot + (slot - bar) / 2;
    if (tall > 0) {
      const r = Math.min(4, bar / 2, tall);
      const y = top + plot - tall;
      // Rounded data end, square on the baseline.
      node.append(
        svg("path", {
          class: "a-chart-bar",
          d: `M${x},${top + plot}V${y + r}Q${x},${y} ${x + r},${y}H${x + bar - r}Q${x + bar},${y} ${x + bar},${y + r}V${top + plot}Z`,
        }),
      );
    }
    const hit = svg("rect", {
      x: left + index * slot,
      y: top,
      width: slot,
      height: plot,
      class: "a-chart-hit",
      tabindex: "-1",
    });
    const show = () => {
      tip.hidden = false;
      tip.textContent = `${row.day} · ${format(row.value, row)}${extra ? " · " + extra(row) : ""}`;
      tip.style.left = `${((left + index * slot + slot / 2) / width) * 100}%`;
    };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("pointerleave", () => (tip.hidden = true));
    node.append(hit);
    if (
      index === 0 ||
      index === rows.length - 1 ||
      index === Math.floor(rows.length / 2)
    ) {
      const label = svg("text", {
        x: x + bar / 2,
        y: height - 6,
        "text-anchor": "middle",
        class: "a-chart-axis",
      });
      label.textContent = row.day.slice(5);
      node.append(label);
    }
  });
  return h("div.a-chart-wrap", node, tip);
}
// Chart with a chart/table switch. columns: extra table columns [{ label, value(row) }].
export function chartWithTable({ title, rows, format, scale, extra, columns }) {
  const body = h("div");
  let mode = "chart";
  const draw = () =>
    body.replaceChildren(
      mode === "chart"
        ? barChart({ title, rows, format, scale, extra })
        : table(
            [
              { label: t("day"), value: (row) => row.day },
              {
                label: title,
                value: (row) => format(row.value, row),
                className: "is-num",
              },
              ...(columns || []),
            ],
            [...rows].reverse(),
          ),
    );
  draw();
  return h(
    "div.a-chart-block",
    h(
      "div.a-chart-head",
      h("h3", title),
      segmented(
        [
          { value: "chart", label: t("chart") },
          { value: "table", label: t("table") },
        ],
        mode,
        (value) => {
          mode = value;
          draw();
        },
        t("view"),
      ),
    ),
    body,
  );
}
