// Atlas owns the presentation; source/status interpretation stays shared with Classic.
import { h, clear } from "./dom.js";
import { badge } from "./ui.js";
import { lang } from "./i18n.js";
import { architecture } from "../home_model.js";
import { issues, pendingItems } from "../home_status.js";
import { draw } from "../home_architecture.js";
import { uiText } from "../i18n/index.js";
import { roleObservation, receiptObservation } from "./architecture_model.js";
const layout = [
  ["intake", "消息接入与归档"],
  ["backend", "后台"],
  ["model", "本地 / 云端模型"],
  ["storage", "状态库"],
  ["review", "解析处理 · 审核"],
  ["backup", "备份"],
  ["vault", "Vault 笔记与附件"],
  ["index", "检索与索引"],
];
const local = (zh, en) => (lang() === "en" ? en : zh);
export function createArchitecture(ctx) {
  const node = h("details.a-card.a-architecture", { open: true });
  const title = h(
    "summary.a-architecture-title",
    local("系统架构与运行状况", "System architecture & runtime"),
  );
  const host = h("div.a-architecture-body");
  node.append(title, host);
  let observer = null,
    records = {};
  function stateText(state) {
    return String(uiText(state.text));
  }
  function go(page) {
    return () =>
      ctx.navigate(
        page === "documents" ? "worklist" : page,
        page === "documents" ? "documents" : "",
      );
  }
  function update(next = records) {
    records = next;
    observer?.disconnect();
    const model = architecture(records, pendingItems(records));
    const map = h("div.a-arch-map.arch-map", {
      role: "group",
      "aria-label": local("资料流向与依赖", "Data flow and dependencies"),
    });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("arch-lines");
    svg.setAttribute("aria-hidden", "true");
    map.append(svg);
    const entries = h("div.a-arch-entries");
    for (const [i, entry] of model.entries.entries())
      entries.append(
        h(
          "button.a-arch-node.arch-entry",
          {
            type: "button",
            dataset: {
              node: "entry:" + i,
              target: entry.page === "channels" ? "intake" : "backend",
              tone: entry.tone,
            },
            onclick: go(entry.page),
          },
          h("strong", String(uiText(entry.label))),
          badge(stateText(entry), entry.tone),
        ),
      );
    map.append(entries);
    for (const [key, label] of layout) {
      const state = model.nodes[key];
      map.append(
        h(
          "button.a-arch-node",
          {
            type: "button",
            dataset: { node: key, tone: state.tone },
            onclick: go(state.page),
          },
          h("strong", String(uiText(label))),
          badge(stateText(state), state.tone),
        ),
      );
    }
    const problems = h("div.a-architecture-issues");
    for (const problem of issues(records))
      problems.append(h("p", { role: "status" }, String(uiText(problem.text))));
    const services = h("div.a-architecture-services");
    const record = records.channels;
    const stale =
      !!record?.error || !record || Date.now() - record.observed > 20000;
    for (const entry of record?.value?.channels || []) {
      const line = h("div.a-architecture-service", h("strong", entry.name));
      for (const [role, label] of [
        ["query", local("查询服务", "Query service")],
        ["intake", local("保存服务", "Save service")],
      ]) {
        if (!entry.services?.roles?.[role]) continue;
        const state = roleObservation(entry.services.roles[role], stale);
        line.append(
          h("span", label + " · "),
          badge(stateText(state), state.tone),
        );
      }
      if (entry.receipts) {
        const state = receiptObservation(entry.receipts, stale);
        line.append(
          h("span", local("回执 · ", "Receipts · ")),
          badge(stateText(state), state.tone),
        );
      }
      services.append(line);
    }
    clear(
      host,
      problems,
      map,
      services,
      h(
        "p.a-subtle",
        local(
          "节点来自各来源最近一次读取，不是实时流量；模型未主动探测。实线：资料流向 · 虚线：依赖调用。",
          "Nodes show the latest recorded reads, not live traffic. Models are not probed. Solid lines: data flow; dashed: dependencies.",
        ),
      ),
    );
    observer = new ResizeObserver(() => draw(map));
    observer.observe(map);
    requestAnimationFrame(() => draw(map));
  }
  node.addEventListener("toggle", () => {
    if (node.open) update();
  });
  const timer = setInterval(() => {
    if (!document.hidden && node.isConnected && node.open) update();
  }, 5000);
  return {
    node,
    update,
    dispose() {
      observer?.disconnect();
      clearInterval(timer);
    },
  };
}
