import { uiText } from "./i18n/index.js";
// Relationship diagram, never a throughput or model-health animation.
import { el } from "./ui.js";
export function renderFlow(records) {
  const root = el("details", null, "home-flow"),
    ns = "http://www.w3.org/2000/svg";
  root.append(el("summary", uiText("资料如何成为知识")));
  root.dataset.layout = window.matchMedia("(max-width: 767px)").matches
    ? "mobile"
    : "desktop";
  root.open = root.dataset.layout === "desktop";
  const steps = [
    ["消息 / 文件", "channels"],
    ["提取整理", "imports"],
    ["审核发布", "worklist"],
    ["知识库", "vaults"],
    ["检索 / Agent", "clients"],
  ];
  const list = el("ol", null, "home-flow-steps");
  for (const [label, page] of steps) {
    const item = el("li"),
      button = el("button", uiText(label));
    button.type = "button";
    button.dataset.pageTarget = page;
    item.append(button);
    if (page !== "clients") {
      const arrow = document.createElementNS(ns, "svg");
      arrow.setAttribute("viewBox", "0 0 32 20");
      arrow.setAttribute("aria-hidden", "true");
      arrow.classList.add("home-flow-arrow");
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", "M3 10h25m-5-5 5 5-5 5");
      arrow.append(path);
      item.append(arrow);
    }
    list.append(item);
  }
  const model = records.query?.value;
  root.append(
    list,
    el(
      "p",
      uiText("实线：资料流向 · 点划线：权限约束 · 虚线：服务依赖"),
      "muted home-legend",
    ),
  );
  const dependencies = el("div", null, "home-dependencies");
  for (const [label, page, kind] of [
    [
      model
        ? model.configured
          ? "模型：已配置，尚未探测"
          : "模型：尚未配置"
        : "模型：尚未确认配置",
      "models",
      "dependency",
    ],
    ["权限：按客户端与知识库授予", "clients", "permission"],
    ["后台：独立于桌面窗口运行", "backend", "dependency"],
  ]) {
    const button = el("button", uiText(label));
    button.type = "button";
    button.dataset.pageTarget = page;
    button.className = kind;
    const connector = document.createElementNS(ns, "svg");
    connector.setAttribute("viewBox", "0 0 32 16");
    connector.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M0 8h32");
    path.setAttribute(
      "stroke-dasharray",
      kind === "permission" ? "8 3 1 3" : "5 4",
    );
    connector.append(path);
    button.prepend(connector);
    dependencies.append(button);
  }
  root.append(dependencies);
  return root;
}
