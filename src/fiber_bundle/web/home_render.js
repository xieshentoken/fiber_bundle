import { phrase, textNode, uiText } from "./i18n/index.js";
// Presentation only; every number keeps its source and scope.
import { el } from "./ui.js";
import { activityRows } from "./home_activity.js";
import { row } from "./ui_cards.js";
import { icon } from "./icons.js";
import { issues, pendingItems } from "./home_status.js";
import {
  architecture,
  agentRows,
  costSummary,
  criticalAlert,
  homeStatus,
  recentImports,
  todoRows,
  vaultRows,
} from "./home_model.js";
import { renderArchitecture } from "./home_architecture.js";
import {
  agentsCard,
  importsCard,
  jump,
  readLabels as labels,
  todoCard,
  vaultsCard,
} from "./home_cards.js";
import { costCard } from "./home_cost.js";
export { jump };

// Header actions beside the greeting: overall state, open count and the inbox shortcut.
export function renderHomeHeader(start, end, records, count) {
  const state = homeStatus(records);
  const chip = el(
    "span",
    uiText(state.text),
    "status-badge home-state " + state.tone,
  );
  const total = el("span", null, "home-count");
  total.append(
    el("strong", Object.keys(records).length ? String(count) : "—"),
    textNode(
      Object.keys(records).length
        ? uiText(" 项待办 · 已读取范围")
        : uiText(" 待办尚未读取"),
    ),
  );
  start.replaceChildren(chip, total);
  const inbox = jump(null, "worklist", "primary home-inbox");
  inbox.append(icon("worklist"), el("span", uiText("打开收件箱")));
  if (count) inbox.append(el("span", String(count), "button-count"));
  end.replaceChildren(inbox);
}

function alertBanner(alert) {
  const banner = el("div", null, "home-alert");
  banner.setAttribute("role", "alert");
  const text = el("div");
  text.append(el("strong", uiText(alert.title)), el("p", uiText(alert.body)));
  banner.append(
    icon("alert"),
    text,
    jump(alert.action, alert.page, "home-alert-action"),
  );
  return banner;
}

let scopeNode = null;
export function renderHome(root, records, build, scope, owner = {}, rerender) {
  scopeNode ??= document.getElementById("home-scope");
  const focused = root.contains(document.activeElement)
    ? document.activeElement
    : null;
  const focusKey = focused
    ? [
        focused.dataset.pageTarget,
        focused.dataset.taskId,
        focused.textContent,
      ].join("|")
    : null;
  const opened = new Map(
    [...root.querySelectorAll("details")].map((d) => [
      d.querySelector("summary")?.textContent,
      d.open,
    ]),
  );
  const todo = todoRows(records);
  const parts = [];
  const alert = criticalAlert(records);
  if (alert) parts.push(alertBanner(alert));
  const system = el("details", null, "home-system-details");
  system.append(el("summary", uiText("系统架构与运行状况")));
  system.append(
    renderArchitecture(
      architecture(records, pendingItems(records)),
      records,
      issues(records),
      labels,
    ),
  );
  const running = activityRows(records).processing;
  if (running.length) {
    const current = el("section", null, "ui-card");
    current.append(el("h3", phrase("正在处理 · {p0}", { p0: running.length })));
    for (const item of running.slice(0, 3))
      current.append(row(item, item.detail));
    current.append(jump("查看全部处理任务", "worklist"));
    parts.push(current);
  }
  parts.push(system);
  const grid = el("div", null, "home-columns");
  const middle = el("div", null, "home-column"),
    right = el("div", null, "home-column");
  middle.append(
    importsCard(recentImports(records), records, scopeNode, owner),
    vaultsCard(vaultRows(records), records),
  );
  right.append(
    costCard(costSummary(records.usage?.value), records.usage, rerender),
    agentsCard(agentRows(records), records),
  );
  grid.append(todoCard(todo, records, scope, rerender), middle, right);
  parts.push(
    grid,
    el(
      "p",
      phrase("本机管理 · {p0} · {p1}", {
        p0: build?.version || uiText("版本未记录"),
        p1: build?.source_sha256?.slice(0, 8) || uiText("构建未记录"),
      }),
      "home-build muted",
    ),
  );
  root.replaceChildren(...parts);
  for (const details of root.querySelectorAll("details")) {
    const previous = opened.get(details.querySelector("summary")?.textContent);
    if (previous !== undefined) details.open = previous;
  }
  if (focusKey)
    [...root.querySelectorAll("button,summary")]
      .find(
        (n) =>
          [n.dataset.pageTarget, n.dataset.taskId, n.textContent].join("|") ===
          focusKey,
      )
      ?.focus({ preventScroll: true });
  return todo.length;
}

export { renderPending } from "./home_pending.js";
