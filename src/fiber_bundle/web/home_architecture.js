import { phrase, setText, textNode, uiText } from "./i18n/index.js";
// System map on the home page: observed node states plus connectors drawn from layout.
// It is a relationship diagram, never a throughput animation or a health probe.
import { el } from "./ui.js";
import { icon } from "./icons.js";
import { shortTime } from "./home_status.js";

const ns = "http://www.w3.org/2000/svg";
const layout = [
  ["intake", "消息接入与归档", "channels"],
  ["backend", "后台", "backend"],
  ["model", "本地 / 云端模型", "models"],
  ["storage", "状态库", "backend"],
  ["review", "解析处理 · 审核", "documents"],
  ["backup", "备份", "backups"],
  ["vault", "Vault 笔记与附件", "vaults"],
  ["index", "检索与索引", "graph"],
];
// [from, to, kind]; entry rows connect by index, "entry:N".
const links = [
  ["intake", "backend", "flow"],
  ["backend", "review", "flow"],
  ["review", "vault", "flow"],
  ["vault", "index", "flow"],
  ["backend", "storage", "flow"],
  ["storage", "backup", "flow"],
  ["model", "backend", "dependency"],
  ["model", "index", "dependency"],
];
const severity = {
  error: 4,
  paused: 3,
  waiting: 2,
  idle: 1,
  success: 0,
  info: 0,
};
function edgeTone(a, b, kind) {
  const worst = severity[a] >= severity[b] ? a : b;
  if (worst === "error") return "fault";
  if (worst === "paused") return "paused";
  if (a === "idle") return "idle";
  return kind;
}
function status(text, tone) {
  const line = el("span", uiText(text), "arch-status");
  line.dataset.tone = tone;
  return line;
}
function nodeButton(key, title, state, extra) {
  const button = el("button", null, "arch-node");
  button.type = "button";
  button.dataset.node = key;
  button.dataset.tone = state.tone;
  button.dataset.pageTarget = state.page;
  button.append(el("strong", uiText(title)), status(state.text, state.tone));
  if (extra) button.append(el("small", uiText(extra)));
  return button;
}

export function renderArchitecture(model, records, problems, labels) {
  const card = el("section", null, "ui-card arch-card");
  card.setAttribute("aria-labelledby", "arch-title");
  const head = el("div", null, "ui-card-head arch-head");
  const title = el("h2", uiText("系统架构与运行状况"));
  title.id = "arch-title";
  // One <p> per root issue; storage failures collapse into a single root.
  const list = el("div", null, "home-issues");
  for (const problem of problems) {
    const line = el(
      "p",
      null,
      problem.key === "storage_unavailable" ? "error" : "warning",
    );
    line.append(
      icon("alert"),
      el(
        "strong",
        problem.key === "storage_unavailable"
          ? uiText("严重：")
          : uiText("注意："),
      ),
      textNode(
        phrase("{p0} · 影响：{p1}", {
          p0: uiText(problem.text),
          p1: uiText(problem.sources.map((s) => labels[s] || s).join("、")),
        }),
      ),
    );
    list.append(line);
  }
  head.append(title, list);
  const map = el("div", null, "arch-map");
  map.setAttribute("role", "group");
  setText(map, uiText("资料流向与依赖，按钮打开对应页面"), "aria-label");
  const entries = el("div", null, "arch-entries");
  const ready = model.entries.filter((e) => e.tone === "success").length;
  const entryHead = el("p", null, "arch-entries-head");
  entryHead.append(
    el("span", uiText("入口")),
    el(
      "span",
      phrase("{p0}/{p1} 已有读取或连接证据", {
        p0: ready,
        p1: model.entries.length,
      }),
    ),
  );
  entries.append(entryHead);
  model.entries.forEach((entry, index) => {
    const row = el("button", null, "arch-entry");
    row.type = "button";
    row.dataset.node = "entry:" + index;
    row.dataset.tone = entry.tone;
    row.dataset.pageTarget = entry.page;
    row.dataset.target = entry.page === "channels" ? "intake" : "backend";
    row.append(
      el("strong", uiText(entry.label)),
      status(entry.text, entry.tone),
    );
    entries.append(row);
  });
  map.append(entries);
  const latest = (records.executions?.value?.runs || [])[0];
  for (const [key, label] of layout) {
    const extra =
      key === "review" && latest
        ? `最近执行 ${shortTime(latest.updated_at || latest.created_at)}`
        : null;
    map.append(nodeButton(key, label, model.nodes[key], extra));
  }
  const svg = document.createElementNS(ns, "svg");
  svg.classList.add("arch-lines");
  svg.setAttribute("aria-hidden", "true");
  map.prepend(svg);
  const legend = el("ul", null, "arch-legend");
  setText(legend, uiText("图例"), "aria-label");
  for (const [kind, text] of [
    ["flow", "数据流"],
    ["dependency", "依赖调用"],
    ["paused", "部分 / 暂停"],
    ["fault", "故障点"],
    ["idle", "无新数据"],
  ]) {
    const item = el("li", uiText(text));
    item.dataset.kind = kind;
    legend.append(item);
  }
  const note = el(
    "p",
    uiText(
      "节点状态来自各来源最近一次读取，不是实时流量；模型未主动探测；“最近执行”为历史执行，非当前连通性检查。",
    ),
    "ui-note",
  );
  card.append(head, map, legend, note);
  watch(map);
  return card;
}

// Connectors follow the rendered boxes, so they stay correct across widths and font sizes.
let observer = null;
function watch(map) {
  observer?.disconnect();
  if (typeof ResizeObserver === "undefined") return;
  observer = new ResizeObserver(() => draw(map));
  observer.observe(map);
}
function box(map, element) {
  const outer = map.getBoundingClientRect(),
    inner = element.getBoundingClientRect();
  return {
    left: inner.left - outer.left,
    right: inner.right - outer.left,
    top: inner.top - outer.top,
    bottom: inner.bottom - outer.top,
    cx: (inner.left + inner.right) / 2 - outer.left,
    cy: (inner.top + inner.bottom) / 2 - outer.top,
  };
}
function path(svg, d, tone) {
  const line = document.createElementNS(ns, "path");
  line.setAttribute("d", d);
  line.dataset.kind = tone;
  svg.append(line);
  return line;
}
function marker(svg, x, y, tone) {
  if (!["fault", "paused"].includes(tone)) return;
  const group = document.createElementNS(ns, "g");
  group.classList.add("arch-marker");
  group.dataset.kind = tone;
  const circle = document.createElementNS(ns, "circle");
  circle.setAttribute("cx", x);
  circle.setAttribute("cy", y);
  circle.setAttribute("r", 7);
  const glyph = document.createElementNS(ns, "path");
  glyph.setAttribute(
    "d",
    tone === "fault"
      ? `M${x - 3},${y - 3}L${x + 3},${y + 3}M${x + 3},${y - 3}L${x - 3},${y + 3}`
      : `M${x - 2},${y - 3}V${y + 3}M${x + 2},${y - 3}V${y + 3}`,
  );
  group.append(circle, glyph);
  svg.append(group);
}
export function draw(map) {
  const svg = map.querySelector(".arch-lines");
  if (!svg || !map.isConnected) return;
  svg.replaceChildren();
  // Stacked (narrow) layout reads top to bottom without connectors.
  if (
    window.getComputedStyle(map).getPropertyValue("--arch-lines").trim() ===
    "none"
  )
    return;
  const nodes = Object.fromEntries(
    [...map.querySelectorAll("[data-node]")].map((n) => [n.dataset.node, n]),
  );
  const tone = (key) => nodes[key]?.dataset.tone || "idle";
  for (const entry of map.querySelectorAll(".arch-entry")) {
    const target = nodes[entry.dataset.target];
    if (!target) continue;
    const a = box(map, entry),
      b = box(map, target),
      kind = edgeTone(entry.dataset.tone, target.dataset.tone, "flow"),
      x1 = a.right,
      x2 = b.left - 2,
      ty = Math.min(Math.max(a.cy, b.top + 10), b.bottom - 10);
    const mid = (x1 + x2) / 2;
    path(svg, `M${x1},${a.cy}C${mid},${a.cy} ${mid},${ty} ${x2},${ty}`, kind);
    marker(svg, x1 + 18, a.cy, kind);
  }
  for (const [from, to, kind] of links) {
    if (!nodes[from] || !nodes[to]) continue;
    const a = box(map, nodes[from]),
      b = box(map, nodes[to]),
      style = edgeTone(tone(from), tone(to), kind);
    let d, mx, my;
    if (from === "model" && to === "index") {
      const top = Math.min(a.top, b.top) - 12;
      d = `M${a.right},${a.cy}C${a.right + 60},${a.cy} ${a.right + 40},${top} ${(a.right + b.cx) / 2},${top}S${b.cx},${top} ${b.cx},${b.top - 2}`;
      mx = (a.right + b.cx) / 2;
      my = top;
    } else if (Math.abs(a.cx - b.cx) < 8) {
      const down = a.bottom <= b.top;
      const y1 = down ? a.bottom : a.top,
        y2 = down ? b.top - 2 : b.bottom + 2;
      d = `M${a.cx},${y1}V${y2}`;
      mx = a.cx;
      my = (y1 + y2) / 2;
    } else if (Math.abs(a.cy - b.cy) < 4) {
      d = `M${a.right},${a.cy}H${b.left - 2}`;
      mx = (a.right + b.left) / 2;
      my = a.cy;
    } else {
      const mid = (a.right + b.left) / 2;
      d = `M${a.right},${a.cy}C${mid},${a.cy} ${mid},${b.cy} ${b.left - 2},${b.cy}`;
      mx = mid;
      my = (a.cy + b.cy) / 2;
    }
    path(svg, d, style);
    marker(svg, mx, my, style);
  }
}
