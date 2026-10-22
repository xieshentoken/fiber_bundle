// Atlas information architecture. Route ids match the classic `#/page` addresses so a
// UI switch keeps the current place.
import { strings } from "./i18n.js";

export const groups = [
  { id: "", pages: ["home", "ask", "worklist"] },
  {
    id: "knowledge",
    pages: ["vaults", "suppliers", "imports", "inventory", "graph", "creation"],
  },
  { id: "connect", pages: ["clients", "channels"] },
  {
    id: "system",
    pages: ["models", "execution", "backend", "backups", "settings"],
  },
];
export const icons = {
  home: "home",
  ask: "ask",
  worklist: "inbox",
  vaults: "vaults",
  suppliers: "vaults",
  imports: "imports",
  inventory: "inventory",
  graph: "graph",
  creation: "creation",
  clients: "clients",
  channels: "channels",
  models: "models",
  execution: "execution",
  backend: "backend",
  backups: "backups",
  settings: "settings",
};
// Classic review sub-pages live inside the Atlas inbox.
export const aliases = {
  pending: ["worklist", "pending"],
  batches: ["worklist", "batches"],
  documents: ["worklist", "documents"],
  media: ["worklist", "media"],
};
export const pageIds = Object.keys(icons);

export const navText = strings({
  zh: {
    group_knowledge: "知识",
    group_connect: "连接",
    group_system: "系统",
    home: "首页",
    ask: "提问与检索",
    worklist: "收件箱",
    vaults: "知识库",
    suppliers: "供应商信息",
    sum_suppliers: "按来源查看供应商、产品与报价；历史原件缺失会明确标注。",
    imports: "资料导入",
    inventory: "资料与版本",
    graph: "关联与 MOC",
    creation: "文档制作",
    clients: "Agent 与客户端",
    channels: "消息入口",
    models: "模型与预算",
    execution: "执行与费用",
    backend: "后台与提醒",
    backups: "备份与恢复",
    settings: "设置",
    sum_home: "只读概览：每项状态都来自最近一次读取，未读取不显示为正常或 0。",
    sum_ask: "检索只返回命中片段与位置；模型查询需明确发起并受预算约束。",
    sum_worklist:
      "按来源查看待你处理的事项；审核、发布与批准逐项确认，不批量自动放行。",
    sum_vaults:
      "默认参与查询与访问授权分别设置。MOC、自动策略及整库合并各自保留预览与确认。",
    sum_imports:
      "保存原件、生成 Draft 和正式发布是独立阶段；普通资料与 fake-expert 分别处理。",
    sum_inventory: "按身份和知识库读取资料与来源版本；组件清单不代表服务在线。",
    sum_graph: "关联建议用于寻找资料，不代表事实已验证；写入前核对预览。",
    sum_creation:
      "文件是未核验草稿，明确保存才成为分析产物；模型执行需专用配置和执行许可。",
    sum_clients:
      "每个客户端独立凭证、独立授权，只授予所需权限；配置、工具可用与问答结果分别验收。",
    sum_channels:
      "保存配置不启动模型或发送消息；暂停入口不影响已有知识，解绑与删除需明确操作。",
    sum_models:
      "每次调用需明确启动，失败即停止；配置存在或历史成功不代表当前可用。",
    sum_execution:
      "云费用按配置单价估算；本地调用独立计数。未知结果先核对，不重发。",
    sum_backend: "桌面窗口和后台独立运行；提醒与停止由你明确发起。",
    sum_backups:
      "后台运行时进行加密备份，配置自动策略并查看进度；恢复只写入新目录。",
    sum_settings: "显示偏好不改变模型、通道或知识库权限。",
  },
  en: {
    group_knowledge: "Knowledge",
    group_connect: "Connect",
    group_system: "System",
    home: "Home",
    ask: "Ask & search",
    worklist: "Inbox",
    vaults: "Vaults",
    suppliers: "Suppliers",
    sum_suppliers: "Source-backed suppliers, products and offers; missing historical originals are labelled.",
    imports: "Import",
    inventory: "Sources & versions",
    graph: "Links & MOC",
    creation: "Documents",
    clients: "Agents & clients",
    channels: "Message channels",
    models: "Models & budget",
    execution: "Runs & cost",
    backend: "Backend & reminders",
    backups: "Backup & restore",
    settings: "Settings",
    sum_home:
      "Read-only overview. Every state comes from the latest read; unread is never shown as healthy or zero.",
    sum_ask:
      "Search returns matching snippets and locations only; model queries are started explicitly and stay within budget.",
    sum_worklist:
      "Everything waiting for you, by source. Reviews, publishing and approvals are confirmed one by one — never auto-approved in bulk.",
    sum_vaults:
      "Default query participation and access grants are set separately. MOC, automation and merges keep their own preview and confirmation.",
    sum_imports:
      "Saving originals, drafting and publishing are separate stages; regular and fake-expert imports are handled separately.",
    sum_inventory:
      "Read sources and versions per identity and vault. A component inventory does not mean a service is online.",
    sum_graph:
      "Link suggestions help you find material; they do not verify facts. Check the preview before writing.",
    sum_creation:
      "Files are unverified drafts until explicitly saved. Model runs need a dedicated configuration and run permission.",
    sum_clients:
      "Each client has its own credential and grants — give only what it needs. Configuration, tool availability and answers are verified separately.",
    sum_channels:
      "Saving a configuration does not start a model or send messages. Pausing keeps existing knowledge; unbinding and removal need explicit action.",
    sum_models:
      "Each call is started explicitly and stops on failure. A saved configuration or past success does not prove current availability.",
    sum_execution:
      "Cloud cost is an estimate from configured prices; local calls are counted separately. Unknown outcomes are reconciled, never re-sent.",
    sum_backend:
      "The desktop window and backend run independently; reminders and stopping are started by you.",
    sum_backups:
      "Create encrypted backups while the backend runs, configure automation and inspect progress. Restore writes to a new directory.",
    sum_settings:
      "Display preferences never change models, channels or vault permissions.",
  },
});
export const pageTitle = (page) => navText(page);
export const pageSummary = (page) => navText("sum_" + page);

// "#/worklist/batches?id=…" → { page, sub, params }
export function parseRoute(hash) {
  const match = /^#\/([a-z]+)(?:\/([a-z-]+))?(?:\?(.*))?$/.exec(hash || "");
  if (!match) return { page: "home", sub: "", params: {} };
  let [, page, sub = "", query = ""] = match;
  if (aliases[page]) [page, sub] = aliases[page];
  if (!icons[page]) return { page: "home", sub: "", params: {} };
  return { page, sub, params: Object.fromEntries(new URLSearchParams(query)) };
}
export function routeHash(page, sub = "", params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    ),
  ).toString();
  return `#/${page}${sub ? "/" + sub : ""}${query ? "?" + query : ""}`;
}
