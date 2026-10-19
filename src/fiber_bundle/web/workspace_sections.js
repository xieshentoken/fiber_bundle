// Assemble local page sections from existing nodes. No feature request or configuration writes.
import { createSections, disclosure } from "./view_sections.js";
const byId = (id) => document.getElementById(id);
function mount(page, entries, options = {}) {
  const host = byId(`${page}-panel`);
  if (host)
    createSections(host, entries, { id: `workspace-${page}`, ...options });
}
export function mountWorkSections() {
  const execution = byId("execution-panel"),
    children = [...execution.children],
    split = children.findIndex((n) =>
      n.classList.contains("execution-records-heading"),
    );
  if (split >= 0)
    mount("execution", [
      { id: "tasks", label: "执行任务", nodes: children.slice(split) },
      { id: "usage", label: "用量与费用", nodes: children.slice(0, split) },
    ]);
  mount("creation", [
    {
      id: "tasks",
      label: "制作任务",
      nodes: [byId("creation-refresh"), byId("creation-content")],
    },
    { id: "prepare", label: "准备文档", nodes: [byId("creation-form")] },
  ]);
  mount("inventory", [
    {
      id: "versions",
      label: "资料与版本",
      nodes: [
        byId("inventory-refresh"),
        byId("inventory-versions"),
        byId("inventory-content"),
      ],
    },
    { id: "settings", label: "提取与处理设置", nodes: [byId("source-policy")] },
  ]);
}
export function mountManagementSections({ vaultScope } = {}) {
  const backend = byId("backend-panel"),
    children = [...backend.children],
    components = byId("components-content"),
    reminders = byId("reminders-content");
  mount("backend", [
    {
      id: "status",
      label: "后台状态",
      nodes: children.filter(
        (n) =>
          ![
            components,
            reminders,
            components.previousElementSibling,
            reminders.previousElementSibling,
          ].includes(n) && n.tagName !== "H2",
      ),
    },
    {
      id: "reminders",
      label: "提醒设置",
      nodes: [reminders.previousElementSibling, reminders],
    },
    {
      id: "components",
      label: "组件与版本",
      nodes: [components.previousElementSibling, components],
    },
  ]);
  const layout = document.querySelector("#settings-panel .settings-layout");
  if (layout)
    createSections(
      layout,
      [
        {
          id: "appearance",
          label: "外观与阅读",
          nodes: [byId("appearance-form")],
        },
        {
          id: "session",
          label: "登录与会话",
          nodes: [byId("session-settings-form")],
        },
      ],
      { id: "workspace-settings" },
    );
  const vaults = byId("vaults-panel"),
    jobs = byId("vault-jobs");
  if (jobs?.previousElementSibling?.tagName === "H3")
    vaults.append(
      disclosure("知识库接入任务与历史", jobs.previousElementSibling, jobs),
    );
  mount(
    "vaults",
    [
      { id: "libraries", label: "知识库列表", nodes: [byId("vault-list")] },
      {
        id: "save",
        label: "保存与处理",
        nodes: [vaultScope?.host("save"), byId("vault-save-panel")],
      },
      {
        id: "layout",
        label: "布局与外部改动",
        nodes: [vaultScope?.host("layout"), byId("vault-placement-panel")],
      },
      {
        id: "admission",
        label: "接入知识库",
        nodes: [byId("vault-admission-panel"), jobs.closest("details")],
      },
    ],
    {
      onSelect: (key) =>
        Promise.resolve().then(() => vaultScope?.activate(key)),
    },
  );
}
