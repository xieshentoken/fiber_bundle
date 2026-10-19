import { setText, uiText } from "./i18n/index.js";
// Connection navigation and focus only; no API calls or authorization decisions.
import { el } from "./ui.js";
import { control } from "./record_ui.js";
import { revealSection } from "./view_sections.js";

export function agentLayout(root, create, refresh) {
  const heading = el("div", null, "section-heading"),
    toolbar = el("div", null, "panel-actions agent-toolbar"),
    loading = el("p", uiText("正在读取连接与模型配置…"), "muted"),
    workspace = el("div", null, "agent-workspace"),
    picker = el("details", null, "agent-picker"),
    summary = el("summary", uiText("已保存的连接")),
    list = el("div", null, "agent-list"),
    detail = el("div", null, "agent-detail"),
    result = el("div", null, "agent-result");
  heading.append(
    el("h3", uiText("Agent 接入")),
    el("p", uiText("独立只读连接；先查询当前资料，再由受管模型生成引用草稿。")),
  );
  list.setAttribute("role", "list");
  setText(list, uiText("已保存的 Agent 连接"), "aria-label");
  detail.setAttribute("role", "region");
  setText(detail, uiText("连接配置与测试"), "aria-label");
  result.id = "agent-result";
  result.tabIndex = -1;
  result.setAttribute("aria-live", "polite");
  const newButton = control("新建连接", create),
    refreshButton = control("刷新连接", refresh);
  loading.hidden = true;
  loading.setAttribute("role", "status");
  newButton.disabled = true;
  newButton.dataset.policyDisabled = "true";
  toolbar.append(newButton, refreshButton);
  picker.append(summary, list);
  workspace.append(picker, detail);
  root.append(heading, toolbar, loading, workspace);
  const narrow = window.matchMedia("(max-width: 1100px)");
  picker.open = !narrow.matches;
  narrow.addEventListener("change", () => {
    picker.open = !narrow.matches;
  });
  return { list, detail, result, summary, loading, newButton, refreshButton };
}

export function focusAgentSection(target) {
  if (!target) return;
  revealSection(target);
  target.tabIndex = -1;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "nearest" });
}

export function sectionLinks(configuration, test, history) {
  const links = el("nav", null, "panel-actions agent-section-links");
  setText(links, uiText("当前连接的详情导航"), "aria-label");
  for (const [label, target] of [
    ["配置与范围", configuration],
    ["问答与结果", test],
    ["操作记录", history],
  ]) {
    if (target) links.append(control(label, () => focusAgentSection(target)));
  }
  return links;
}
