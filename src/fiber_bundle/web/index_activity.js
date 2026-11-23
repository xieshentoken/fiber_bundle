// One global observation survives navigation; it neither starts work nor owns connectivity.
import {
  indexDependencyText,
  indexDependencyTitle,
} from "./note_index_errors.js";
import { locale, onLocaleChange } from "./i18n/index.js";
import { validIndexActivity } from "./index_activity_api.js";

const copy = {
  zh: {
    title: "知识库索引状态",
    loading: "正在读取索引状态…",
    unavailable: "索引状态暂不可用",
    empty: "索引 · 无已接入知识库",
    ready: "索引已就绪",
    updating: "索引更新中 · {n} 个库",
    blocked: "等待归档核验 · {n} 个库",
    blockedState: "等待归档核验",
    blockedGeneric: "等待操作核验 · {n} 个库",
    blockedCount: "{n} 个库等待归档核验",
    waiting: "索引等待中 · {n} 个库",
    failed: "索引更新失败 · {n} 个库",
    review: "索引部分可用 · {n} 个库待核对",
    unverified: "索引状态待确认 · {n} 个库",
    failedCount: "{n} 个库失败",
    waitingCount: "{n} 个库等待",
    reviewCount: "{n} 个库待核对",
    unverifiedCount: "{n} 个库状态待确认",
    pending: "待检查 {n} 个路径",
    attention: "需核对 {n} 项",
    observed: "观察于 {time}",
    previous: "上次观察于 {time}，当前状态尚未确认。",
    readFailed: "本次读取未完成，保留上次观察。",
    detail: "查看知识库",
    observing: "正在核对文件变化",
    queued: "等待后台更新索引",
    packages: "正在核对 Draft 包",
    building: "正在更新索引",
    waitingState: "等待同步或重试",
    failedState: "索引更新失败",
    needs_review: "部分可用，需核对",
    readyState: "已就绪",
    unverifiedState: "状态尚未确认",
  },
  en: {
    title: "Vault index status",
    loading: "Reading index status…",
    unavailable: "Index status unavailable",
    empty: "Index · no connected vaults",
    ready: "Indexes ready",
    updating: "Updating indexes · {n} vault(s)",
    blocked: "Waiting for archive verification · {n} vault(s)",
    blockedState: "Waiting for archive verification",
    blockedGeneric: "Waiting for operation review · {n} vault(s)",
    blockedCount: "{n} vault(s) await archive verification",
    waiting: "Indexes waiting · {n} vault(s)",
    failed: "Index update failed · {n} vault(s)",
    review: "Indexes partially available · {n} vault(s) need review",
    unverified: "Index status unconfirmed · {n} vault(s)",
    failedCount: "{n} vault(s) failed",
    waitingCount: "{n} vault(s) waiting",
    reviewCount: "{n} vault(s) need review",
    unverifiedCount: "{n} vault(s) unconfirmed",
    pending: "{n} paths pending checks",
    attention: "{n} item(s) need review",
    observed: "Observed at {time}",
    previous: "Last observed at {time}; current status is unconfirmed.",
    readFailed: "This read did not finish; keeping the last observation.",
    detail: "View vault",
    observing: "Checking file changes",
    queued: "Queued for background index update",
    packages: "Checking Draft packages",
    building: "Updating index",
    waitingState: "Waiting for sync or retry",
    failedState: "Index update failed",
    needs_review: "Partially available; review needed",
    readyState: "Ready",
    unverifiedState: "Status unconfirmed",
  },
};
function text(key, values = {}) {
  return copy[locale() === "en" ? "en" : "zh"][key].replace(
    /\{(\w+)\}/g,
    (_, name) => String(values[name]),
  );
}
const activeStates = new Set(["observing", "queued", "building"]);
const stateLabels = {
  observing: "observing",
  queued: "queued",
  building: "building",
  waiting: "waitingState",
  blocked: "blockedState",
  failed: "failedState",
  needs_review: "needs_review",
  ready: "readyState",
  unverified: "unverifiedState",
};

export function indexActivitySummary({
  value,
  stale = false,
  loading = false,
}) {
  if (stale) return { state: "unverified", label: text("unavailable") };
  if (!value)
    return {
      state: "unverified",
      label: text(loading ? "loading" : "unavailable"),
    };
  const rows = value.vaults;
  if (!rows.length) return { state: "empty", label: text("empty") };
  const counts = Object.fromEntries(
    ["failed", "blocked", "waiting", "needs_review", "unverified"].map(
      (state) => [state, rows.filter((row) => row.state === state).length],
    ),
  );
  const active = rows.filter((row) => activeStates.has(row.state)).length;
  let state = "ready",
    key = "ready",
    n = 0;
  if (active)
    [state, key, n] = [
      counts.failed ? "failed" : "updating",
      "updating",
      active,
    ];
  else if (counts.failed) [state, key, n] = ["failed", "failed", counts.failed];
  else if (counts.blocked)
    [state, key, n] = ["blocked", "blocked", counts.blocked];
  else if (counts.waiting)
    [state, key, n] = ["waiting", "waiting", counts.waiting];
  else if (counts.needs_review)
    [state, key, n] = ["needs_review", "review", counts.needs_review];
  else if (counts.unverified)
    [state, key, n] = ["unverified", "unverified", counts.unverified];
  if (
    key === "blocked" &&
    rows.some(
      (row) =>
        row.state === "blocked" &&
        row.dependency?.recovery !== "verify_committed_archives",
    )
  )
    key = "blockedGeneric";
  const pending = rows.every((row) => row.pending_paths !== null)
    ? rows.reduce((sum, row) => sum + row.pending_paths, 0)
    : null;
  const parts = [text(key, { n })];
  for (const [kind, countKey] of [
    ["failed", "failedCount"],
    ["waiting", "waitingCount"],
    ["blocked", "blockedCount"],
    ["needs_review", "reviewCount"],
    ["unverified", "unverifiedCount"],
  ])
    if (counts[kind] && (active || kind !== state))
      parts.push(text(countKey, { n: counts[kind] }));
  if (Number.isSafeInteger(pending) && pending > 0)
    parts.push(text("pending", { n: pending }));
  return { state, label: parts.join(" · ") };
}

// Requests are serialized and aborted on stop. Older sessions/hidden-window reads cannot publish.
export function createIndexActivityMonitor(read, publish, interval = 15000) {
  let active = false,
    epoch = 0,
    timer = null,
    running = null,
    controller = null,
    value = null,
    stale = false;
  const show = (loading = false) => publish({ value, stale, loading });
  function refresh() {
    if (running) return running;
    const version = epoch,
      request = new AbortController();
    controller = request;
    if (!value) show(true);
    const pending = Promise.resolve()
      .then(() => read({ signal: request.signal }))
      .then((result) => {
        if (version !== epoch) return;
        if (!validIndexActivity(result)) throw Error("invalid_index_activity");
        value = result;
        stale = false;
        show();
      })
      .catch(() => {
        if (version !== epoch) return;
        stale = true;
        show();
      })
      .finally(() => {
        if (version !== epoch || running !== pending) return;
        running = null;
        controller = null;
        clearTimeout(timer);
        if (active) timer = setTimeout(refresh, interval);
      });
    running = pending;
    return pending;
  }
  return {
    refresh,
    start() {
      if (active) return;
      active = true;
      void refresh();
    },
    stop({ clear = false } = {}) {
      active = false;
      epoch++;
      clearTimeout(timer);
      controller?.abort();
      controller = null;
      running = null;
      if (clear) {
        value = null;
        stale = false;
        publish(null);
      } else if (value) {
        stale = true;
        show();
      }
    },
  };
}

function element(tag, className = "", content = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = content;
  return node;
}
function observedTime(value) {
  return new Intl.DateTimeFormat(locale() === "en" ? "en-US" : "zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Taipei",
  }).format(new Date(value * 1000));
}

// Classic's footer opens upward inside an overflow-clipped sidebar. Use the
// actual remaining space so a short window scrolls the menu rather than clipping it.
export function positionFooterIndexActivity(node, summary, panel) {
  const sidebar = node.closest?.(".sidebar-footer")?.closest(".sidebar");
  if (!sidebar) {
    if (panel.style) panel.style.maxHeight = "";
    return;
  }
  const visibleTop = Math.max(8, sidebar.getBoundingClientRect().top + 8),
    available = Math.max(
      0,
      summary.getBoundingClientRect().top - 8 - visibleTop,
    );
  panel.style.maxHeight = `${Math.floor(Math.min(520, window.innerHeight * 0.65, available))}px`;
}

export function createIndexActivity({ read, onOpen }) {
  const node = element("details", "index-activity");
  node.hidden = true;
  const summary = element("summary", "index-activity-summary");
  const indicator = element("i", "index-activity-indicator");
  indicator.setAttribute("aria-hidden", "true");
  const label = element("span");
  label.setAttribute("role", "status");
  label.setAttribute("aria-live", "polite");
  summary.append(indicator, label);
  const panel = element("div", "index-activity-panel");
  node.append(summary, panel);
  const position = () => {
    if (node.open) positionFooterIndexActivity(node, summary, panel);
  };
  node.addEventListener("toggle", position);
  globalThis.window?.addEventListener("resize", position);
  let observation = null;
  function render(next = observation) {
    observation = next;
    node.hidden = !observation;
    if (!observation) {
      panel.replaceChildren();
      node.open = false;
      return;
    }
    const state = indexActivitySummary(observation);
    node.dataset.state = state.state;
    summary.setAttribute("aria-label", text("title") + ": " + state.label);
    summary.title = state.label;
    if (label.textContent !== state.label) label.textContent = state.label;
    panel.replaceChildren();
    panel.append(element("strong", "index-activity-title", text("title")));
    if (observation.stale)
      panel.append(element("p", "index-activity-note", text("readFailed")));
    if (observation.value) {
      panel.append(
        element(
          "p",
          "index-activity-note",
          text(observation.stale ? "previous" : "observed", {
            time: observedTime(observation.value.observed_at),
          }),
        ),
      );
      for (const row of observation.value.vaults) {
        const item = element("div", "index-activity-vault");
        item.dataset.state = row.state;
        item.append(element("strong", "index-activity-name", row.name));
        const parts = [
          row.state === "blocked"
            ? indexDependencyTitle(row.dependency)
            : text(
                row.phase === "packages" ? "packages" : stateLabels[row.state],
              ),
        ];
        if (row.pending_paths > 0)
          parts.push(text("pending", { n: row.pending_paths }));
        if (row.needs_attention > 0)
          parts.push(text("attention", { n: row.needs_attention }));
        item.append(element("p", "index-activity-stage", parts.join(" · ")));
        if (row.dependency)
          item.append(
            element(
              "p",
              "index-activity-note",
              indexDependencyText(row.dependency),
            ),
          );
        const open = element("button", "index-activity-open", text("detail"));
        open.type = "button";
        open.setAttribute("aria-label", text("detail") + " · " + row.name);
        open.addEventListener("click", () => {
          node.open = false;
          onOpen(row.vault_id);
        });
        item.append(open);
        panel.append(item);
      }
    }
    position();
  }
  const monitor = createIndexActivityMonitor(read, render);
  const stopLocale = onLocaleChange(() => render());
  return {
    node,
    start: monitor.start,
    stop: monitor.stop,
    refresh: monitor.refresh,
    dispose() {
      monitor.stop({ clear: true });
      globalThis.window?.removeEventListener("resize", position);
      stopLocale();
    },
  };
}
